import os
import json
import time
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, UTC
from loguru import logger
from tqdm import tqdm
from requests.adapters import HTTPAdapter
from utils.GithubRepo import GithubRepo
from utils.utils import TokenManager
from config import GITHUB_TOKENS
# Run with: python -m get_info.download_workflow_logs

class ProgressManager:
    """Track download progress for resume."""
    
    def __init__(self, progress_file: str):
        self.progress_file = progress_file
        self.task_status = self._load_progress()
        self.lock = threading.Lock()
        # Batch disk writes
        self.save_counter = 0
        self.save_threshold = 500  # persist every N updates
    
    def _load_progress(self) -> dict:
        """Load saved task state."""
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file) as f:
                    data = json.load(f)
                    return data.get('task_status', {})
            except Exception as e:
                logger.error(f"[ERROR] Failed to load progress file {self.progress_file}: {e}")
                return {}
        return {}
    
    def is_completed(self, task_id: str) -> bool:
        """Whether a task finished."""
        return task_id in self.task_status and self.task_status[task_id]['status'] in ['completed', 'expired', 'not_found']
    
    def get_task_status(self, task_id: str) -> str:
        """Return task status string."""
        if task_id in self.task_status:
            return self.task_status[task_id]['status']
        return 'pending'
    
    def mark_completed(self, task_id: str, status: str = 'completed', details: str | None = None):
        """Record task status."""
        with self.lock:
            self.task_status[task_id] = {
                'status': status,
                'timestamp': datetime.now().isoformat(),
                'details': details
            }
            self.save_counter += 1
            
            # Persist every N updates
            if self.save_counter >= self.save_threshold:
                self._save_progress()
                self.save_counter = 0
    
    def force_save(self):
        """Flush pending progress to disk."""
        with self.lock:
            if self.save_counter > 0:
                self._save_progress()
                self.save_counter = 0
                logger.info(f"💾 Force saved progress after {self.save_counter} updates")
    
    def scan_completed_downloads(self, output_dir: str) -> int:
        """Scan output dir and mark existing ZIPs completed
        
        Args:
            output_dir: download output directory
            
        Returns:
            number of tasks newly marked completed
        """
        if not os.path.exists(output_dir):
            logger.warning(f"Output directory {output_dir} does not exist")
            return 0
        
        new_completed = 0
        with self.lock:
            for filename in os.listdir(output_dir):
                if filename.endswith('.zip'):
                    # Filename: owner#repo#workflow_id#run_id.zip
                    # task_id without .zip
                    task_id = filename.replace('.zip', '')
                    filepath = os.path.join(output_dir, filename)
                    
                    # Prefer ctime, else mtime
                    try:
                        # Use creation time when available
                        stat_info = os.stat(filepath)
                        file_time = stat_info.st_ctime  # creation time
                        if file_time == 0:  # fallback to mtime
                            file_time = stat_info.st_mtime
                        
                        # ISO timestamp
                        file_timestamp = datetime.fromtimestamp(file_time).isoformat()
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to get file time for {filename}: {e}, using current time")
                        file_timestamp = datetime.now().isoformat()
                    
                    # Already tracked?
                    if task_id not in self.task_status:
                        # Mark new completion
                        self.task_status[task_id] = {
                            'status': 'completed',
                            'timestamp': file_timestamp,
                            'details': f'Found existing file: {filename}'
                        }
                        new_completed += 1
                        logger.info(f"➕ Added existing download: {task_id} (file time: {file_timestamp})")
                    elif self.task_status[task_id]['status'] not in ['completed', 'expired', 'not_found']:
                        # Upgrade non-terminal status
                        old_status = self.task_status[task_id]['status']
                        self.task_status[task_id] = {
                            'status': 'completed',
                            'timestamp': file_timestamp,
                            'details': f'Updated from {old_status} to completed, found existing file: {filename}'
                        }
                        new_completed += 1
                        logger.info(f"🔄 Updated status for existing download: {task_id} ({old_status} -> completed, file time: {file_timestamp})")
            
            if new_completed > 0:
                self._save_progress()
                logger.info(f"📝 Scanned output directory and added/updated {new_completed} completed tasks")
        
        return new_completed
    
    def _save_progress(self):
        """Write progress JSON."""
        data = {
            'task_status': self.task_status,
            'last_updated': datetime.now().isoformat(),
            'summary': self._get_summary(),
            'save_info': {
                'save_counter': self.save_counter,
                'save_threshold': self.save_threshold,
                'last_save': datetime.now().isoformat()
            }
        }
        with open(self.progress_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.debug(f"💾 Progress saved (counter: {self.save_counter}/{self.save_threshold})")
    
    def _get_summary(self) -> dict:
        """Aggregate status counts."""
        summary = {
            'total_tasks': len(self.task_status),
            'completed': 0,
            'expired': 0,
            'not_found': 0,
            'failed': 0,
            'pending': 0
        }
        
        for _, info in self.task_status.items():
            status = info['status']
            if status in summary:
                summary[status] += 1
        
        return summary

class WorkflowLogDownloader:
    """Download workflow run log archives."""
    
    def __init__(self, token_manager: TokenManager, max_workers: int = 5, 
                    progress_file: str = 'results/download_progress.json', 
                    output_dir: str = 'results/workflow_detail_logs',
                    input_dir: str = 'results/workflow_runs',
                    ):
        self.token_manager = token_manager
        self.max_workers = max_workers
        self.progress_manager = ProgressManager(progress_file)
        self.output_dir = output_dir
        self.input_dir = input_dir
        
        # HTTP session lifecycle
        self.session = None
        self.session_requests_count = 0
        self.session_reset_threshold = 1000  # reset session interval
        self.session_lock = threading.Lock()
        
        # Initial session
        self._create_new_session()
        
        # Live counters
        self.stats_lock = threading.Lock()
        self.realtime_stats = {
            'successful_downloads': 0,
            'expired_410': 0,
            'not_found_404': 0,
            'access_denied_403': 0,
            'other_errors': 0,
            'total_processed': 0
        }
        # Per-repo earliest expired log date
        self.expired_repo_dates = {}
        self.expired_lock = threading.Lock()
        
        # Stall detection
        self.last_activity_time = time.time()
        self.activity_lock = threading.Lock()
    
    def _update_activity(self):
        """Touch activity timestamp."""
        with self.activity_lock:
            self.last_activity_time = time.time()
    
    def _check_deadlock(self):
        """Detect long idle periods."""
        with self.activity_lock:
            time_since_last_activity = time.time() - self.last_activity_time
            if time_since_last_activity > 300:  # 5 minutes idle
                logger.warning(f"⚠️ Possible deadlock detected: {time_since_last_activity:.1f}s since last activity")
                return True
        return False
    
    def _create_new_session(self):
        """Create a fresh requests session."""
        if self.session:
            try:
                self.session.close()
            except Exception as e:
                logger.warning(f"⚠️ Failed to close session: {e}")
                pass
        
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'WorkflowLogDownloader/1.0'
        })
        
        # Proxy from env
        proxies = {}
        if os.environ.get('http_proxy'):
            proxies['http'] = os.environ.get('http_proxy')
        if os.environ.get('https_proxy'):
            proxies['https'] = os.environ.get('https_proxy')
        
        if proxies:
            self.session.proxies.update(proxies)
            logger.info(f"🌐 Using proxy: {proxies}")
        else:
            logger.info("🌐 No proxy configured")
        
        # Connection pool
        adapter = HTTPAdapter(
            pool_connections=10,       # pool size
            pool_maxsize=20,          # max connections
            max_retries=2,            # retries
            pool_block=False          # do not block when pool full
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
        self.session_requests_count = 0
        logger.debug("🔄 Created new session")
    
    def _get_session(self):
        """Return session, rotating when needed."""
        # Check reset flag under lock
        should_reset = False
        with self.session_lock:
            if self.session_requests_count >= self.session_reset_threshold:
                should_reset = True
                self.session_requests_count = 0
        
        # Reset outside lock
        if should_reset:
            logger.info(f"🔄 Resetting session after {self.session_reset_threshold} requests")
            self._create_new_session()
        
        # Bump request counter
        with self.session_lock:
            self.session_requests_count += 1
            return self.session
    
    def get_session_status(self):
        """Session usage stats."""
        with self.session_lock:
            return {
                'requests_count': self.session_requests_count,
                'reset_threshold': self.session_reset_threshold,
                'next_reset_in': self.session_reset_threshold - self.session_requests_count
            }
    
    def get_proxy_status(self):
        """Proxy configuration."""
        proxies = {}
        if os.environ.get('http_proxy'):
            proxies['http'] = os.environ.get('http_proxy')
        if os.environ.get('https_proxy'):
            proxies['https'] = os.environ.get('https_proxy')
        
        return {
            'proxies': proxies,
            'has_proxy': bool(proxies),
            'session_proxies': getattr(self.session, 'proxies', {}) if self.session else {}
        }
    
    def _update_stats(self, stat_type: str):
        """Increment live counters."""
        with self.stats_lock:
            if stat_type in self.realtime_stats:
                self.realtime_stats[stat_type] += 1
            self.realtime_stats['total_processed'] += 1
    
    def _record_expired_repo_date(self, owner: str, repo: str, created_at: str):
        """Remember expired log dates to skip older runs
        
        Note: once a date expires, all earlier dates expire too
        e.g. if Jul 1 logs are gone, retention is shorter than days since Jul 1
        so Jun 25, Jun 20, etc. are also expired
        
        Args:
            owner: repo owner
            repo: repo name
            created_at: ISO timestamp
        """
        if not created_at:
            return
        
        try:
            # Parse date portion
            run_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            date_key = run_date.date().isoformat()
            repo_key = f"{owner}#{repo}"
            
            with self.expired_lock:
                # Keep the latest expired date
                # Older dates need no update
                should_update = True
                if repo_key in self.expired_repo_dates:
                    latest_existing = self.expired_repo_dates[repo_key]
                    if date_key <= latest_existing:
                        # No update needed
                        should_update = False
                        logger.debug(f"📅 Skipped recording earlier expired date for {repo_key}: {date_key} (existing: {latest_existing})")
                
                if should_update:
                    # Store expired date
                    self.expired_repo_dates[repo_key] = date_key
                    logger.info(f"📅 Updated expired date for {repo_key}: {date_key}")
                else:
                    logger.debug(f"📅 Kept existing expired date for {repo_key}: {latest_existing}")
                    
        except Exception as e:
            logger.warning(f"⚠️ Failed to record expired date for {owner}#{repo}: {e}")
    
    def _is_repo_date_expired(self, owner: str, repo: str, created_at: str) -> bool:
        """Whether a run date is known expired
        
        If a later date expired, earlier dates are expired too
        e.g. Jul 1 expired implies Jun 25 is expired
        
        Args:
            owner: repo owner
            repo: repo name
            created_at: ISO timestamp
            
        Returns:
            True if the repo date is expired, False otherwise
        """
        if not created_at:
            return False
        
        try:
            # Parse date portion
            run_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            date_key = run_date.date().isoformat()
            repo_key = f"{owner}#{repo}"
            
            # Short lock hold
            latest_expired = None
            with self.expired_lock:
                if repo_key not in self.expired_repo_dates:
                    return False
                # Latest expired date string
                latest_expired = self.expired_repo_dates[repo_key]
            
            # Date <= stored cutoff means expired
            return date_key <= latest_expired
                
        except Exception as e:
            logger.warning(f"⚠️ Failed to check expired date for {owner}#{repo}: {e}")
            return False
    
    def _get_expired_repo_dates_summary(self) -> dict:
        """Summarize per-repo expired dates."""
        with self.expired_lock:
            summary = {
                'total_expired_repos': len(self.expired_repo_dates),
                'total_expired_dates': len(self.expired_repo_dates),  # one cutoff per repo
                'expired_repos': {}
            }
            
            for repo_key, expired_date in self.expired_repo_dates.items():
                summary['expired_repos'][repo_key] = {
                    'expired_dates': 1,  # one cutoff per repo
                    'dates': [expired_date]
                }
            
            return summary
    
    def _print_progress(self, total_tasks: int):
        """Log live progress."""
        with self.stats_lock:
            stats = self.realtime_stats.copy()
        
        progress_percent = (stats['total_processed'] / total_tasks * 100) if total_tasks > 0 else 0
        logger.info(f"📊 Progress: {stats['total_processed']}/{total_tasks} ({progress_percent:.1f}%) | "
              f"✅ {stats['successful_downloads']} | "
              f"⚠️ 410: {stats['expired_410']} | "
              f"❌ 404: {stats['not_found_404']} | "
              f"🔒 403: {stats['access_denied_403']} | "
              f"💥 Other: {stats['other_errors']}")
    
    def scan_existing_downloads(self):
        """Scan downloads and sync progress file."""
        logger.info("🔍 Scanning existing downloads in output directory...")
        new_completed = self.progress_manager.scan_completed_downloads(self.output_dir)
        if new_completed > 0:
            logger.info(f"✅ Found {new_completed} existing downloads, added to task status")
        else:
            logger.info("ℹ️ No new existing downloads found")
    
    def _handle_rate_limit(self, response: requests.Response) -> bool:
        """Handle GitHub rate limiting."""
        if response.status_code == 403:
            reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
            if reset_time > 0:
                wait_time = reset_time - time.time() + 10
                if wait_time > 0:
                    logger.warning(f"Rate limit exceeded. Waiting {wait_time:.0f} seconds...")
                    time.sleep(wait_time)
                    return True
        return False
    
    def _download_log(self, owner: str, repo: str, workflow_id: str, run_id: str, run_attempt: str, created_at: str, total_tasks: int) -> bool:
        """Download logs for one workflow run."""
        # Touch activity
        self._update_activity()
        
        task_id = f"{owner}#{repo}#{workflow_id}#{run_id}"

        # Skip known-expired dates
        if self._is_repo_date_expired(owner, repo, created_at):
            # Mark done to avoid retries
            self.progress_manager.mark_completed(task_id, 'expired', 'Skipped: repo date already expired (410)')
            self._update_stats('expired_410')
            logger.debug(f"⏭️ Skipped expired run: {task_id} (repo date already expired)")
            return True

        # Skip if already completed
        if self.progress_manager.is_completed(task_id):
            return True

        # Attach token
        token = self.token_manager.get_token()
        session = self._get_session()
        session.headers['Authorization'] = f'token {token}'
        
        # Build API URL
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{run_attempt}/logs"
        
        try:
            # Resolve redirect URL
            response = session.get(url, allow_redirects=False, timeout=30)
            
            # Rate limit
            if self._handle_rate_limit(response):
                response = session.get(url, allow_redirects=False, timeout=30)
            
            # Handle status codes
            if response.status_code == 302:
                download_url = response.headers.get('Location')
                if download_url:
                    # Download ZIP
                    log_response = session.get(download_url, stream=True, timeout=60)
                    if log_response.status_code == 200:
                        # Write ZIP
                        os.makedirs(self.output_dir, exist_ok=True)
                        filename = f"{owner}#{repo}#{workflow_id}#{run_id}.zip"
                        filepath = os.path.join(self.output_dir, filename)
                        
                        with open(filepath, 'wb') as f:
                            for chunk in log_response.iter_content(chunk_size=8192):
                                f.write(chunk)
                        
                        # Mark completed
                        self.progress_manager.mark_completed(task_id, 'completed', f'Downloaded successfully: {filename}')
                        self._update_stats('successful_downloads')
                        # logger.success(f"Downloaded: {filename}")
                        self._print_progress(total_tasks)
                        return True
                    else:
                        logger.error(f"❌ Failed to download logs for {task_id}: {log_response.status_code}")
                        self._update_stats('other_errors')
                        self._print_progress(total_tasks)
                else:
                    logger.error(f"❌ No download URL for {task_id}")
                    self._update_stats('other_errors')
                    self._print_progress(total_tasks)
            elif response.status_code == 410:
                # 410: gone, mark done
                self.progress_manager.mark_completed(task_id, 'expired', 'Log expired or not found (410)')
                self._update_stats('expired_410')
                logger.warning(f"⚠️ Log expired/not found for {task_id} (410)")
                
                # Remember expired date
                if created_at:
                    self._record_expired_repo_date(owner, repo, created_at)
                
                self._print_progress(total_tasks)
                return True
            elif response.status_code == 404:
                # 404: mark done
                self.progress_manager.mark_completed(task_id, 'not_found', 'Resource not found (404)')
                self._update_stats('not_found_404')
                logger.warning(f"⚠️ Resource not found for {task_id} (404)")
                self._print_progress(total_tasks)
                return True
            elif response.status_code == 403:
                # 403: retry later
                self._update_stats('access_denied_403')
                logger.warning(f"🔒 Access denied for {task_id} (403)")
                self._print_progress(total_tasks)
                return False
            else:
                logger.error(f"❌ Failed to get download URL for {task_id}: {response.status_code}")
                self._update_stats('other_errors')
                self._print_progress(total_tasks)
                
        except Exception as e:
            logger.error(f"❌ Error downloading {task_id}: {e}")
            self._update_stats('other_errors')
            self._print_progress(total_tasks)
        
        return False
    
    def _parse_workflow_runs(self, input_dir: str) -> list[dict]:
        """Build download tasks from runs JSON."""
        tasks = []
        from datetime import datetime, timedelta
        
        # Only runs within last 90 days (UTC)
        cutoff_date = datetime.now(UTC) - timedelta(days=90)
        
        for filename in tqdm(os.listdir(input_dir), desc="Parsing workflow runs files", ncols=100):
            if filename.endswith('#runs.json'):
                filepath = os.path.join(input_dir, filename)
                if "neondatabase#neon" in filepath:
                    continue
                try:
                    with open(filepath) as f:
                        data = json.load(f)
                    
                    # Repo from filename
                    repo_name = filename.replace('#runs.json', '')
                    github_repo = GithubRepo(repo_name)
                    owner = github_repo.owner
                    repo = github_repo.reponame
                    
                    # Walk runs
                    # Shape: {workflow_id: {"runs": [...]}}
                    for workflow_id, workflow_data in data.items():
                        if isinstance(workflow_data, dict) and 'runs' in workflow_data:
                            for run in workflow_data['runs']:
                                run_id = run.get('id')
                                run_attempt = run.get('run_attempt', 1)
                                created_at = run.get('created_at')
                                status = run.get('status')
                                conclusion = run.get('conclusion')
                                if status != 'completed':
                                    continue
                                if status == 'completed' and conclusion == 'success' or status == 'completed' and conclusion == 'skipped':
                                    continue

                                # Drop runs older than cutoff
                                if created_at:
                                    try:
                                        run_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                        if run_date < cutoff_date:
                                            continue  # skip old run
                                    except Exception as e:
                                        logger.warning(f"[WARNING] Failed to parse date {created_at}: {e}")
                                        pass  # keep run if date parse fails
                                
                                if run_id:
                                    tasks.append({
                                        'owner': owner,
                                        'repo': repo,
                                        'workflow_id': workflow_id,
                                        'run_id': run_id,
                                        'run_attempt': run_attempt,
                                        'created_at': created_at
                                    })
                
                except Exception as e:
                    logger.error(f"❌ Error parsing {filename}: {e}")
        
        return tasks
    
    def download_all_logs(self):
        """Download all pending workflow logs."""
        # Sync existing ZIPs first
        self.scan_existing_downloads()
        
        logger.info("🔍 Parsing workflow runs files...")
        tasks = self._parse_workflow_runs(self.input_dir)
        
        if not tasks:
            logger.error("❌ No tasks found")
            return
        
        logger.info(f"📋 Found {len(tasks)} workflow runs to download")
        
        # Pending tasks only
        pending_tasks = []
        for task in tasks:
            task_id = f"{task['owner']}#{task['repo']}#{task['workflow_id']}#{task['run_id']}"
            
            # Skip completed
            if self.progress_manager.is_completed(task_id):
                continue
            
            pending_tasks.append(task)
        
        logger.info(f"📥 {len(pending_tasks)} tasks pending, {len(tasks) - len(pending_tasks)} already completed")
        
        if not pending_tasks:
            logger.success("All tasks already completed")
            return
        
        # Thread pool download
        logger.info(f"🚀 Starting download with {self.max_workers} workers...")
        
        # Reset session before pool
        logger.info("🔄 Emergency recovery: resetting all locks and session...")
        self._create_new_session()
        self._update_activity()
        
        logger.info("📊 Real-time progress statistics: ✅ = Successful downloads, ⚠️ 410 = Expired/Not found logs, "
                         "❌ 404 = Resource not found, 🔒 403 = Access denied, 💥 Other = Other errors")
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit futures
            future_to_task = {
                executor.submit(
                    self._download_log,
                    task['owner'],
                    task['repo'],
                    task['workflow_id'],
                    task['run_id'],
                    task['run_attempt'],
                    task['created_at'],
                    len(pending_tasks)
                ): task for task in pending_tasks
            }
            
            # Drain futures
            for future in as_completed(future_to_task):
                # Stall check
                if self._check_deadlock():
                    logger.warning("⚠️ Deadlock detected, attempting to recover...")
                    # Force new session
                    self._create_new_session()
                
                task = future_to_task[future]
                try:
                    future.result()  # handled in _download_log
                except Exception as e:
                    logger.error(f"❌ Task failed: {task}: {e}")
                    self._update_stats('other_errors')
                    self._print_progress(len(pending_tasks))
        
        # Final summary
        logger.info("🎉 Download completed!")
        
        # Flush progress
        self.progress_manager.force_save()
        
        # Session stats
        session_status = self.get_session_status()
        logger.info(f"📡 Session status: {session_status['requests_count']} requests, "
                   f"next reset in {session_status['next_reset_in']} requests")
        
        # Proxy stats
        proxy_status = self.get_proxy_status()
        if proxy_status['has_proxy']:
            logger.info(f"🌐 Proxy status: {proxy_status['proxies']}")
        else:
            logger.info("🌐 No proxy configured")
        
        # Task status breakdown
        summary = self.progress_manager._get_summary()
        logger.info(f"📊 Final task status summary: Completed: {summary['completed']}, "
                         f"Expired (410): {summary['expired']}, "
                         f"Not found (404): {summary['not_found']}, "
                         f"Failed: {summary['failed']}, "
                         f"Pending: {summary['pending']}, "
                         f"Total: {summary['total_tasks']}")
        
        # Expired-date summary
        expired_summary = self._get_expired_repo_dates_summary()
        if expired_summary['total_expired_repos'] > 0:
            logger.info(f"📅 Expired repo summary: {expired_summary['total_expired_repos']} repos with {expired_summary['total_expired_dates']} expired dates")
            for repo_key, info in expired_summary['expired_repos'].items():
                logger.info(f"   📍 {repo_key}: {info['expired_dates']} expired dates ({', '.join(info['dates'])})")
        
        with self.stats_lock:
            final_stats = self.realtime_stats.copy()
        
        logger.info(f"📊 Real-time statistics from this session: Successful downloads: {final_stats['successful_downloads']}, "
                         f"410 (Expired/Not found): {final_stats['expired_410']}, "
                         f"404 (Resource not found): {final_stats['not_found_404']}, "
                         f"403 (Access denied): {final_stats['access_denied_403']}, "
                         f"Other errors: {final_stats['other_errors']}, "
                         f"Total processed: {final_stats['total_processed']}")

def view_task_status(progress_file = 'results/download_progress.json'):
    """Print progress file summary."""
    
    if not os.path.exists(progress_file):
        logger.error("❌ Progress file not found")
        return
    
    try:
        with open(progress_file) as f:
            data = json.load(f)
        
        summary = data.get('summary', {})
        last_updated = data.get('last_updated', 'Unknown')
        
        logger.info("📊 Task Status Report")
        logger.info(f"Last updated: {last_updated}")
        logger.info(f"Total tasks: {summary.get('total_tasks', 0)}")
        for status, count in summary.items():
            if status != 'total_tasks' and count > 0:
                logger.info(f"{status.capitalize()}: {count}")

    except Exception as e:
        logger.error(f"❌ Error reading progress file: {e}")

def search_task(task_id: str, progress_file = 'results/download_progress.json'):
    """Look up one task in progress file."""
    
    if not os.path.exists(progress_file):
        logger.error("❌ Progress file not found")
        return
    
    try:
        with open(progress_file) as f:
            data = json.load(f)
        
        task_status = data.get('task_status', {})
        
        if task_id in task_status:
            info = task_status[task_id]
            logger.info(f"🔍 Task: {task_id}")
            logger.info(f"Status: {info['status']}")
            logger.info(f"Timestamp: {info['timestamp']}")
            if info.get('details'):
                logger.info(f"Details: {info['details']}")
        else:
            logger.warning(f"❌ Task {task_id} not found in progress file")
    
    except Exception as e:
        logger.error(f"❌ Error reading progress file: {e}")

def scan_existing_downloads_standalone(output_dir: str = 'results/workflow_detail_logs', 
                                     progress_file: str = 'results/download_progress.json'):
    """Standalone: scan output and update progress
    
    Args:
        output_dir: download output directory
        progress_file: progress JSON path
    """
    logger.info("🔍 Standalone scan: Scanning existing downloads in output directory...")
    
    if not os.path.exists(progress_file):
        logger.error(f"❌ Progress file {progress_file} not found")
        return
    
    if not os.path.exists(output_dir):
        logger.error(f"❌ Output directory {output_dir} not found")
        return
    
    # Scan with ProgressManager
    progress_manager = ProgressManager(progress_file)
    new_completed = progress_manager.scan_completed_downloads(output_dir)
    
    if new_completed > 0:
        logger.success(f"✅ Scan completed! Found and added {new_completed} existing downloads to task status")
        
        # Print updated summary
        summary = progress_manager._get_summary()
        logger.info(f"📊 Updated task status summary: Completed: {summary['completed']}, "
                         f"Expired (410): {summary['expired']}, "
                         f"Not found (404): {summary['not_found']}, "
                         f"Failed: {summary['failed']}, "
                         f"Pending: {summary['pending']}, "
                         f"Total: {summary['total_tasks']}")
    else:
        logger.info("ℹ️ No new existing downloads found")

def detail_workflow_logs_download(token_manager: TokenManager, input_dir: str='results/workflow_runs', 
                                  output_dir: str='results/workflow_detail_logs',max_workers: int = 5):
    """Entry point"""

    logger.info("🚀 Workflow Log Downloader")
    progress_file = os.path.join(os.path.dirname(input_dir), 'download_progress.json')
    downloader = WorkflowLogDownloader(token_manager, max_workers, progress_file, output_dir, input_dir)
    
    try:
        # Run downloads
        downloader.download_all_logs()
    except KeyboardInterrupt:
        logger.warning("\n⚠️ Download interrupted by user")
        logger.info("Progress has been saved. You can resume later.")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")

    # search_task(task_id, progress_file)
    # view_task_status(progress_file) 

def filter_successful_workflow_runs(workflow_logs_dir: str = 'results/workflow_detail_logs',
                                   workflow_runs_dir: str = 'results/workflow_runs',
                                   output_dir: str = 'results/filtered_workflow_logs'):
    """Copy only failed-run log ZIPs to output
    
    Args:
        workflow_logs_dir: directory of downloaded ZIP logs
        workflow_runs_dir: runs JSON directory
        output_dir: destination for failed-run ZIPs only
    """
    logger.info("🔍 Filtering workflow logs based on workflow runs records...")
    
    # Ensure output dir
    os.makedirs(output_dir, exist_ok=True)
    
    # Map run_id -> status
    run_status_map = {}
    logger.info("📖 Reading workflow runs records...")
    
    for filename in tqdm(os.listdir(workflow_runs_dir), desc="Reading workflow runs", ncols=100):
        if not filename.endswith('#runs.json'):
            continue
        
        filepath = os.path.join(workflow_runs_dir, filename)
        try:
            with open(filepath) as f:
                data = json.load(f)
            
            # Parse each workflow block
            for _, workflow_data in data.items():
                if isinstance(workflow_data, dict) and 'runs' in workflow_data:
                    for run in workflow_data['runs']:
                        run_id = str(run.get('id', ''))
                        status = run.get('status')
                        conclusion = run.get('conclusion')
                        
                        # Store run status
                        run_status_map[run_id] = {
                            'status': status,
                            'conclusion': conclusion,
                            'source_file': filename
                        }
        except Exception as e:
            logger.error(f"❌ Error reading {filename}: {e}")
    
    logger.info(f"📊 Found {len(run_status_map)} workflow runs in records")
    
    # Filter ZIP files
    kept_files = 0
    moved_files = 0
    total_files = 0
    
    logger.info("🔍 Processing workflow log files...")
    
    for filename in tqdm(os.listdir(workflow_logs_dir), desc="Processing workflow logs", ncols=100):
        if not filename.endswith('.zip'):
            continue
        
        total_files += 1
        filepath = os.path.join(workflow_logs_dir, filename)
        
        # Parse ZIP filename
        parts = filename.replace('.zip', '').split('#')
        if len(parts) < 4:
            logger.warning(f"⚠️ Invalid filename format: {filename}")
            continue
        
        run_id = parts[3]
        
        # Require known run_id
        if run_id not in run_status_map:
            logger.warning(f"⚠️ Run ID {run_id} not found in workflow runs records: {filename}")
            continue
        
        run_info = run_status_map[run_id]
        status = run_info['status']
        conclusion = run_info['conclusion']
        
        # Keep non-success completed runs
        should_keep = False
        if status == 'completed' and conclusion != 'success':
            should_keep = True
            logger.debug(f"✅ Keeping failed run: {filename} (status: {status}, conclusion: {conclusion})")
        else:
            logger.debug(f"⏭️ Skipping successful/ongoing run: {filename} (status: {status}, conclusion: {conclusion})")
        
        if should_keep:
            # Copy to output
            output_filepath = os.path.join(output_dir, filename)
            try:
                # shutil.copy2
                import shutil
                shutil.copy2(filepath, output_filepath)
                kept_files += 1
            except Exception as e:
                logger.error(f"❌ Error copying {filename}: {e}")
        else:
            moved_files += 1
    
    logger.info("🎉 Filtering completed!")
    logger.info(f"📊 Total files: {total_files}")
    logger.info(f"✅ Kept files (failed runs): {kept_files}")
    logger.info(f"⏭️ Skipped files (successful/ongoing runs): {moved_files}")
    logger.info(f"📁 Output directory: {output_dir}")
    
    # Sample kept files
    if kept_files > 0:
        logger.info("📋 Sample kept files (failed runs):")
        kept_files_list = [f for f in os.listdir(output_dir) if f.endswith('.zip')][:5]
        for filename in kept_files_list:
            run_id = filename.replace('.zip', '').split('#')[3]
            if run_id in run_status_map:
                run_info = run_status_map[run_id]
                logger.info(f"   {filename}: status={run_info['status']}, conclusion={run_info['conclusion']}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--scan':
        # --scan mode
        logger.info("🔍 Running in scan mode...")
        scan_existing_downloads_standalone()
    elif len(sys.argv) > 1 and sys.argv[1] == '--filter':
        # --filter mode
        logger.info("🔍 Running in filter mode...")
        filter_successful_workflow_runs()
    else:
        # default download mode
        logger.info("🚀 Running in download mode...")
        token_manager = TokenManager(GITHUB_TOKENS)
        detail_workflow_logs_download(token_manager, max_workers=8) 