# Repo list file: 100+_repos.json
import json
import os
import requests
import time
import glob
from loguru import logger
from requests.exceptions import RequestException, Timeout, ConnectionError, HTTPError
import concurrent.futures

from utils.GithubRepo import GithubRepo
from utils.utils import TokenManager
from config import GITHUB_TOKENS

# Run with: python -m get_info.get_workflow_info_of_repos


class RateLimitHandler:
    """Handle GitHub API rate limits."""
    
    def __init__(self):
        self.rate_limit_reset_time = 0
        self.rate_limit_remaining = 5000
        self.rate_limit_limit = 5000
    
    def update_from_headers(self, headers):
        """Update rate limit from response headers."""
        if 'X-RateLimit-Remaining' in headers:
            self.rate_limit_remaining = int(headers['X-RateLimit-Remaining'])
        if 'X-RateLimit-Limit' in headers:
            self.rate_limit_limit = int(headers['X-RateLimit-Limit'])
        if 'X-RateLimit-Reset' in headers:
            self.rate_limit_reset_time = int(headers['X-RateLimit-Reset'])
    
    def should_wait(self) -> bool:
        """Whether a wait is required."""
        return self.rate_limit_remaining <= 10
    
    def get_wait_time(self) -> int:
        """Seconds to wait before retrying."""
        if self.rate_limit_reset_time == 0:
            return 60  # default 1 minute
        
        current_time = int(time.time())
        wait_time = max(0, self.rate_limit_reset_time - current_time) + 10  # extra 10s buffer
        return wait_time
    
    def log_status(self):
        """Log current rate limit status."""
        logger.info(f"Rate Limit: {self.rate_limit_remaining}/{self.rate_limit_limit}")


def get_completed_repos(save_path: str) -> set[str]:
    """List repos already processed."""
    completed = set()
    if not os.path.exists(save_path):
        return completed
    
    # Find saved workflow JSON files
    pattern = os.path.join(save_path, '*#workflows.json')
    for file_path in glob.glob(pattern):
        # Repo key from filename
        filename = os.path.basename(file_path)
        repo_name = filename.replace('#workflows.json', '')
        completed.add(repo_name)
    
    logger.info(f"Found {len(completed)} completed repos")
    return completed


def get_workflow_info(github_token: str, repo: GithubRepo, save_path: str = 'results/workflow_info', 
                     max_retries: int = 3, retry_delay: int = 1, rate_limit_handler: RateLimitHandler | None = None):
    """
    Fetch all workflow metadata for a repo
    """
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    params = {
        'per_page': 100
    }

    api_url = f"https://api.github.com/repos/{repo.url_name}/actions/workflows"

    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching workflows for {repo.url_name} (attempt {attempt + 1}/{max_retries})")
            resp = requests.get(api_url, headers=headers, params=params, timeout=30)

            # Update rate limit state
            if rate_limit_handler:
                rate_limit_handler.update_from_headers(resp.headers)
                rate_limit_handler.log_status()

            if resp.status_code == 200:
                workflows = resp.json().get('workflows', [])
                save_workflow_data(repo, workflows, save_path)
                logger.info(f"Fetched {len(workflows)} workflows for {repo.url_name}")
                return True
            elif resp.status_code == 404:
                logger.warning(f"Repo {repo.url_name} not found or inaccessible")
                return False
            elif resp.status_code == 403:
                if rate_limit_handler and rate_limit_handler.should_wait():
                    wait_time = rate_limit_handler.get_wait_time()
                    logger.warning(f"Rate limit hit, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"API limit or permission denied - {repo.url_name}: {resp.status_code}")
                    if attempt < max_retries - 1:
                        logger.info(f"Retrying in {retry_delay * 60}s...")
                        time.sleep(retry_delay * 60)
                    continue
            elif resp.status_code == 401:
                logger.error(f"Invalid or expired token - {repo.url_name}")
                return False
            else:
                logger.error(f"Failed to fetch workflows - {repo.url_name}: {resp.status_code}, {resp.text}")

        except Timeout:
            logger.warning(f"Request timeout - {repo.url_name} (attempt {attempt + 1}/{max_retries})")
        except ConnectionError:
            logger.warning(f"Connection error - {repo.url_name} (attempt {attempt + 1}/{max_retries})")
        except HTTPError as e:
            logger.error(f"HTTP error - {repo.url_name}: {e} (attempt {attempt + 1}/{max_retries})")
        except RequestException as e:
            logger.error(f"Request error - {repo.url_name}: {e} (attempt {attempt + 1}/{max_retries})")
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error - {repo.url_name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unknown error - {repo.url_name}: {e} (attempt {attempt + 1}/{max_retries})")

        if attempt < max_retries - 1:
            logger.info(f"Retrying in {retry_delay}s...")
            time.sleep(retry_delay)

    logger.error(f"All retries failed - {repo.url_name}")
    return False


def save_workflow_data(repo: GithubRepo, workflows: list, save_path: str):
    """
    Save workflow JSON to disk
    """
    try:
        save_path = os.path.join(save_path, f'{repo.file_name}#workflows.json')
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(workflows, f, indent=4, ensure_ascii=False)

    except OSError as e:
        logger.error(f"File error - {repo.url_name}: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"JSON encode error - {repo.file_name}: {e}")
    except Exception as e:
        logger.error(f"Unknown error saving file - {repo.url_name}: {e}")


def parallel_get_workflow_info(repos: list[str], token_manager: TokenManager, 
                             max_workers: int = 8, save_path: str = 'results/workflow_info'):
    """
    Fetch workflows for many repos concurrently with resume
    
    Args:
        repos: list of repo URLs
        token_manager: token pool
        max_workers: worker count
        save_path: output directory
    """
    # Completed repos
    completed_repos = get_completed_repos(save_path)
    
    # Pending repos
    pending_repos = []
    for repo_url in repos:
        repo = GithubRepo(repo_url)
        if repo.file_name not in completed_repos:
            pending_repos.append(repo_url)
        else:
            logger.info(f"Skipping completed repo: {repo.file_name}")
    
    if not pending_repos:
        logger.info("All repos already completed")
        return
    
    logger.info(f"Pending {len(pending_repos)} repos, skipped {len(completed_repos)} completed")
    
    # Shared rate limit handler
    rate_limit_handler = RateLimitHandler()
    
    def task(repo_url):
        repo = GithubRepo(repo_url)
        token = token_manager.get_token()
        success = get_workflow_info(token, repo, save_path, rate_limit_handler=rate_limit_handler)
        
        if success:
            logger.info(f"Done: {repo.file_name}")
        else:
            logger.error(f"Failed: {repo.file_name}")
        
        return success
    
    # Thread pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(task, repo_url) for repo_url in pending_repos]
        
        completed_count = 0
        failed_count = 0
        
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                if result:
                    completed_count += 1
                else:
                    failed_count += 1
                
                # Progress
                total_processed = completed_count + failed_count
                logger.info(f"Progress: {total_processed}/{len(pending_repos)} "
                          f"(ok: {completed_count}, failed: {failed_count})")
                
            except Exception as e:
                logger.error(f"[Concurrent task error] {e}")
                failed_count += 1
    
    logger.info(f"Done: {completed_count} succeeded, {failed_count} failed")


if __name__ == '__main__':
    repo_file = '100+_repos.json'
    save_path = 'results/workflow_info'
    
    # Load repo list
    try:
        with open(repo_file, encoding='utf-8') as f:
            data = json.load(f)
        repos = [repo['url'] for repo in data['repos']]
    except FileNotFoundError:
        logger.error(f"Repo list not found: {repo_file}")
        exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid repo list JSON: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"Error reading repo list: {e}")
        exit(1)
    
    logger.info(f"Total repos: {len(repos)}")
    
    # Token manager
    token_manager = TokenManager(GITHUB_TOKENS)
    
    try:
        # Resume support
        enable_resume = True
        if os.path.exists(save_path) and os.listdir(save_path):
            logger.info("Existing output found; resume enabled")
        else:
            logger.info("No prior output; starting fresh")
        
        # Run
        parallel_get_workflow_info(
            repos=repos,
            token_manager=token_manager,
            max_workers=8,
            save_path=save_path
        )

    except KeyboardInterrupt:
        logger.warning("\nInterrupted by user")
        logger.info("Progress is saved; rerun to resume")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        logger.info("Finished")
