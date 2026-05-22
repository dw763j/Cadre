# From project root with .venv activated, e.g.:
#   source .venv/bin/activate && python -m utils.RepoCloner --help
"""Batch clone or update GitHub repositories (CLI entry: ``main()``)."""

import argparse
import concurrent.futures
import json
import os
import sys
from pathlib import Path

import subprocess
from loguru import logger
from tqdm import tqdm

from config import GITHUB_PROXY


class RepoCloner:
    def __init__(self, output_dir="./repos", max_workers=5, proxy="", timeout=300):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_workers = max_workers
        self.timeout = timeout
        self.proxy = proxy
        self.cloned_repos = set()
        self.failed_repos = {}
        self.state_file = self.output_dir / ".clone_state.json"
        self.stop_due_to_disk = False

        # Setup logging
        logger.add(self.output_dir / "clone.log")

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Load previous state if exists
        self._load_state()
    
    def _load_state(self):
        """Load previous cloning state from file"""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                    self.cloned_repos = set(state.get('cloned_repos', []))
                    self.failed_repos = state.get('failed_repos', {})
                    logger.info(f"Loaded state: {len(self.cloned_repos)} cloned, {len(self.failed_repos)} failed")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
    
    def _save_state(self):
        """Save current cloning state to file"""
        try:
            state = {
                'cloned_repos': list(self.cloned_repos),
                'failed_repos': self.failed_repos
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def _check_disk_space(self, path: Path, min_gb=50):
        """Check free disk space in GB for the given path"""
        stat = os.statvfs(str(path))
        free_bytes = stat.f_bavail * stat.f_frsize
        free_gb = free_bytes / (1024 ** 3)
        return free_gb

    def clone_repo(self, repo_url):
        """Clone a single repository"""
        # Check disk space
        free_gb = self._check_disk_space(self.output_dir, 50)
        if free_gb < 50:
            logger.warning(f"Insufficient disk space: {free_gb:.2f}GB free under {self.output_dir}, below 50GB threshold. Will stop after current repo.")
            self.stop_due_to_disk = True

        try:
            # Extract user and repo name
            parts = repo_url.strip().rstrip('/').split('/')
            if len(parts) < 2:
                raise ValueError(f"Invalid repository URL format: {repo_url}")
            
            repo_name = parts[-1]
            user_name = parts[-2]
            
            # Create directory name in format "user#repo"
            target_dir = self.output_dir / f"{user_name}#{repo_name}"
            
            # Skip if already cloned
            if repo_url in self.cloned_repos:
                logger.info(f"Skipping already cloned: {repo_url}")
                return True
            
            # Delete directory if it exists but is in failed state
            if target_dir.exists() and repo_url in self.failed_repos:
                logger.info(f"Cleaning failed clone attempt for: {repo_url}")
                subprocess.run(['rm', '-rf', str(target_dir)], check=True)
            
            # Clone the repository
            logger.info(f"Cloning {repo_url} to {target_dir}")
            
            result = subprocess.run(
                ['git', 'clone', f"{self.proxy}/{repo_url}" if self.proxy else repo_url, str(target_dir)],
                capture_output=True, 
                text=True, 
                timeout=self.timeout
            )
            
            if result.returncode != 0:
                logger.error(f"Failed to clone {repo_url}: {result.stderr}")
                self.failed_repos[repo_url] = result.stderr
                return False
            
            # Add to cloned repos and remove from failed if it was there
            self.cloned_repos.add(repo_url)
            if repo_url in self.failed_repos:
                del self.failed_repos[repo_url]
                
            return True
            
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout while cloning {repo_url}")
            self.failed_repos[repo_url] = "Timeout"
            return False
        except Exception as e:
            logger.error(f"Error cloning {repo_url}: {str(e)}")
            self.failed_repos[repo_url] = str(e)
            return False
    
    def clone_repos(self, repos_list):
        """Clone multiple repositories in parallel"""
        total_repos = len(repos_list)
        logger.info(f"Starting to clone {total_repos} repositories with {self.max_workers} workers")
        
        # Filter out already cloned repos
        to_clone = [repo for repo in repos_list if repo not in self.cloned_repos]
        logger.info(f"{len(to_clone)} repositories to clone ({len(repos_list) - len(to_clone)} already cloned)")
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all tasks
                future_to_repo = {executor.submit(self.clone_repo, repo): repo for repo in to_clone}
                
                # Process results with progress bar
                with tqdm(total=len(to_clone), desc="Cloning repositories") as pbar:
                    for future in concurrent.futures.as_completed(future_to_repo):
                        repo_url = future_to_repo[future]
                        try:
                            success = future.result()
                            if success:
                                logger.success(f"Successfully cloned: {repo_url}")
                            else:
                                logger.warning(f"Failed to clone: {repo_url}")
                        except Exception as e:
                            logger.error(f"Exception while cloning {repo_url}: {e}")
                            self.failed_repos[repo_url] = str(e)
                        
                        # Save state periodically
                        self._save_state()
                        pbar.update(1)
                        # Stop early if disk space is insufficient
                        if self.stop_due_to_disk:
                            logger.error("Insufficient disk space detected; stopping remaining clone tasks.")
                            print("Insufficient disk space detected; stopping remaining clone tasks.")
                            break
        
        except KeyboardInterrupt:
            logger.warning("Interrupted by user, saving current state...")
            self._save_state()
            sys.exit(1)
        
        # Final save
        self._save_state()
        
        # Summary
        logger.info(f"Cloning completed. Successfully cloned: {len(self.cloned_repos)}/{total_repos}")
        if self.failed_repos:
            logger.warning(f"Failed to clone {len(self.failed_repos)} repositories: {self.failed_repos}")
            
        # Return statistics
        return {
            'total': total_repos,
            'cloned': len(self.cloned_repos),
            'failed': len(self.failed_repos)
        }
    
    def retry_failed(self):
        """Retry previously failed repositories"""
        if not self.failed_repos:
            logger.info("No failed repositories to retry")
            return {'total': 0, 'cloned': 0, 'failed': 0}
        
        failed_repos = list(self.failed_repos.keys())
        logger.info(f"Retrying {len(failed_repos)} failed repositories")
        return self.clone_repos(failed_repos)

    def update_repo(self, repo_url):
        """Update a single repository by pulling latest changes"""
        try:
            # Extract user and repo name to find the local directory
            parts = repo_url.strip().rstrip('/').split('/')
            if len(parts) < 2:
                raise ValueError(f"Invalid repository URL format: {repo_url}")
            
            repo_name = parts[-1]
            user_name = parts[-2]
            
            # Find the local directory
            target_dir = self.output_dir / f"{user_name}#{repo_name}"
            
            if not target_dir.exists():
                logger.warning(f"Repository directory not found: {target_dir}")
                return False
            
            if not (target_dir / ".git").exists():
                logger.warning(f"Not a git repository: {target_dir}")
                return False
            
            # Change to the repository directory and pull
            logger.info(f"Updating {repo_url} in {target_dir}")
            
            # First fetch all branches and tags
            fetch_result = subprocess.run(
                ['git', 'fetch', '--all', '--tags'],
                cwd=str(target_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            if fetch_result.returncode != 0:
                logger.error(f"Failed to fetch {repo_url}: {fetch_result.stderr}")
                return False
            
            # Then pull the current branch
            pull_result = subprocess.run(
                ['git', 'pull'],
                cwd=str(target_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            if pull_result.returncode != 0:
                logger.error(f"Failed to pull {repo_url}: {pull_result.stderr}")
                return False
            
            logger.success(f"Successfully updated: {repo_url}")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout while updating {repo_url}")
            return False
        except Exception as e:
            logger.error(f"Error updating {repo_url}: {str(e)}")
            return False

    def update_all_repos(self):
        """Update all cloned repositories in parallel"""
        if not self.cloned_repos:
            logger.info("No repositories to update")
            return {'total': 0, 'updated': 0, 'failed': 0}
        
        repos_to_update = list(self.cloned_repos)
        total_repos = len(repos_to_update)
        logger.info(f"Starting to update {total_repos} repositories with {self.max_workers} workers")
        
        updated_count = 0
        failed_count = 0
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all update tasks
                future_to_repo = {executor.submit(self.update_repo, repo): repo for repo in repos_to_update}
                
                # Process results with progress bar
                with tqdm(total=total_repos, desc="Updating repositories") as pbar:
                    for future in concurrent.futures.as_completed(future_to_repo):
                        repo_url = future_to_repo[future]
                        try:
                            success = future.result()
                            if success:
                                updated_count += 1
                            else:
                                failed_count += 1
                        except Exception as e:
                            logger.error(f"Exception while updating {repo_url}: {e}")
                            failed_count += 1
                        
                        pbar.update(1)
        
        except KeyboardInterrupt:
            logger.warning("Interrupted by user during update...")
            sys.exit(1)
        
        # Summary
        logger.info(f"Update completed. Successfully updated: {updated_count}/{total_repos}")
        if failed_count > 0:
            logger.warning(f"Failed to update {failed_count} repositories")
            
        # Return statistics
        return {
            'total': total_repos,
            'updated': updated_count,
            'failed': failed_count
        }


def _load_repo_urls_from_json(path: Path) -> list[str]:
    repo_list = []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    repos = data.get("repos")
    if not isinstance(repos, list):
        raise ValueError("JSON root object must contain a 'repos' array")
    for item in repos:
        if isinstance(item, dict) and "url" in item:
            repo_list.append(item["url"])
        elif isinstance(item, str):
            repo_list.append(item)
    return repo_list


def main(argv: list[str] | None = None) -> int:
    """CLI entry: batch clone / update cloned repos / retry failed clones from JSON."""
    p = argparse.ArgumentParser(description="Clone/update GitHub repos; state stored in output-dir/.clone_state.json")
    p.add_argument("-o", "--output-dir", default="repos", help="Clone output directory")
    p.add_argument("-i", "--input-json", type=Path, help="Repos JSON (each item has url); use with --clone")
    p.add_argument("--max-workers", type=int, default=16, help="Number of concurrent threads")
    p.add_argument("--timeout", type=int, default=300, help="Per-repo git timeout in seconds")
    p.add_argument("--proxy", default=GITHUB_PROXY, help="Clone URL prefix; empty string for direct connection")
    p.add_argument("--clone", action="store_true", help="Batch clone from --input-json")
    p.add_argument("--update", action="store_true", help="git fetch/pull for recorded repos")
    p.add_argument("--retry-failed", action="store_true", help="Retry previously failed clones")
    args = p.parse_args(argv)

    if not (args.clone or args.update or args.retry_failed):
        p.error("Specify at least one operation: --clone / --update / --retry-failed")
    if args.clone and not args.input_json:
        p.error("--clone requires --input-json")

    repos: list[str] = []
    if args.clone:
        try:
            repos = _load_repo_urls_from_json(args.input_json)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"Failed to read input JSON: {e}", file=sys.stderr)
            return 1
        print(f"Repos to clone: {len(repos)}")

    proxy = args.proxy or None
    cloner = RepoCloner(
        output_dir=args.output_dir,
        max_workers=args.max_workers,
        proxy=proxy,
        timeout=args.timeout,
    )

    if args.update:
        print("UPDATE: Updating cloned repositories...")
        stats = cloner.update_all_repos()
        print(f"Update finished: {stats['updated']} succeeded, {stats['failed']} failed, {stats['total']} total")

    if args.retry_failed:
        stats = cloner.retry_failed()
        print(f"Retry finished: {stats['cloned']} succeeded, {stats['failed']} still failed")

    if args.clone:
        stats = cloner.clone_repos(repos)
        print(f"Clone finished: {stats['cloned']} succeeded, {stats['failed']} failed, {stats['total']} total")
        logger.info(f"Cloning completed: {stats['cloned']} succeeded, {stats['failed']} failed out of {stats['total']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())