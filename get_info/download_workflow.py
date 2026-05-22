# Run with: python -m get_info.download_workflow
import json
import os
import base64
import requests
import time
import concurrent.futures
from loguru import logger
from requests.exceptions import Timeout, ConnectionError

from config import GITHUB_TOKENS, proxied_github_url
from utils.utils import TokenManager


class GitHubWorkflowDownloader:
    def __init__(self):
        self.token_manager = TokenManager(GITHUB_TOKENS)
        self.session = requests.Session()
    
    def get_headers(self):
        """Get request headers."""
        token = self.token_manager.get_token()
        return {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json'
        }
    
    def download_workflow_file(self, owner, repo, workflow_path, ref=None, max_retries=3):
        """Download a single workflow file."""
        # # Try API download first
        # result = self._download_via_api(owner, repo, workflow_path, ref, max_retries)
        # if result:
        #     return result
        
        # # If API fails, try raw URL download
        # logger.info(f"API download failed, trying raw URL: {owner}/{repo}/{workflow_path}")
        return self._download_via_raw(owner, repo, workflow_path, max_retries)
    
    def _download_via_api(self, owner, repo, workflow_path, ref=None, max_retries=3):
        """Download via GitHub API."""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{workflow_path}"
        if ref:
            url += f"?ref={ref}"
        
        for attempt in range(max_retries):
            try:
                headers = self.get_headers()
                response = self.session.get(url, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    content = data['content']
                    # GitHub API returns base64-encoded content
                    
                    decoded_content = base64.b64decode(content).decode('utf-8')
                    return {
                        'content': decoded_content,
                        'sha': data.get('sha'),
                        'path': workflow_path,
                        'method': 'api'
                    }
                elif response.status_code == 404:
                    logger.warning(f"Workflow file not found: {owner}/{repo}/{workflow_path}")
                    return None
                elif response.status_code == 403:
                    logger.warning(f"API rate limit or insufficient permissions - {owner}/{repo}/{workflow_path}: {response.status_code}")
                    if attempt < max_retries - 1:
                        time.sleep(60)  # wait longer for rate limit
                    continue
                elif response.status_code == 401:
                    logger.warning(f"Token authentication failed - {owner}/{repo}/{workflow_path}: {response.status_code}")
                    return None  # do not retry 401
                else:
                    logger.error(f"API download failed - {owner}/{repo}/{workflow_path}: {response.status_code}")
                    
            except (Timeout, ConnectionError) as e:
                logger.warning(f"Network error - {owner}/{repo}/{workflow_path}: {e} (attempt {attempt + 1}/{max_retries})")
            except Exception as e:
                logger.error(f"Unknown error - {owner}/{repo}/{workflow_path}: {e}")
                break
            
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # exponential backoff
        
        return None
    
    def _download_via_raw(self, owner, repo, workflow_path, max_retries=3):
        """Download via raw URL."""
        # Try different branches
        branches = ['main', 'master']
        
        for branch in branches:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{workflow_path}"
            url = proxied_github_url(raw_url)
            
            for attempt in range(max_retries):
                try:
                    response = self.session.get(url, timeout=30)
                    
                    if response.status_code == 200:
                        return {
                            'content': response.text,
                            'sha': None,  # raw URL cannot provide SHA
                            'path': workflow_path,
                            'method': 'raw',
                            'branch': branch
                        }
                    elif response.status_code == 404:
                        logger.debug(f"File not found on branch {branch}: {owner}/{repo}/{workflow_path}")
                        break  # try next branch
                    else:
                        logger.warning(f"Raw download failed - {owner}/{repo}/{workflow_path} (branch: {branch}): {response.status_code}")
                        
                except (Timeout, ConnectionError) as e:
                    logger.warning(f"Network error - {owner}/{repo}/{workflow_path} (branch: {branch}): {e} (attempt {attempt + 1}/{max_retries})")
                except Exception as e:
                    logger.error(f"Unknown error - {owner}/{repo}/{workflow_path} (branch: {branch}): {e}")
                    break
                
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        
        return None
    
    def download_repo_workflows(self, repo_info_file, save_path='results/workflows'):
        """Download all workflows for a single repo."""
        try:
            # Parse filename to get repo info
            filename = os.path.basename(repo_info_file)
            if not filename.endswith('#workflows.json'):
                logger.warning(f"Skipping non-workflow info file: {filename}")
                return
            
            repo_name = filename.replace('#workflows.json', '')
            owner, repo = repo_name.split('#', 1)
            
            logger.info(f"Starting download of workflows for {owner}/{repo}...")
            
            # Read workflow info
            with open(repo_info_file, encoding='utf-8') as f:
                workflows = json.load(f)
            
            if not workflows:
                logger.info(f"{owner}/{repo} has no workflows")
                return
            
            # Create save directory
            repo_save_dir = os.path.join(save_path, repo_name)
            os.makedirs(repo_save_dir, exist_ok=True)
            
            downloaded_count = 0
            failed_count = 0
            
            # Download each workflow file
            for workflow in workflows:
                workflow_path = workflow.get('path')
                if not workflow_path:
                    continue
                
                # Download workflow file content
                result = self.download_workflow_file(owner, repo, workflow_path)
                
                if result:
                    # Save workflow file
                    workflow_filename = os.path.basename(workflow_path)
                    workflow_save_path = os.path.join(repo_save_dir, workflow_filename)
                    
                    try:
                        with open(workflow_save_path, 'w', encoding='utf-8') as f:
                            f.write(result['content'])
                        
                        # Save workflow metadata
                        # metadata = {
                        #     'workflow_info': workflow,
                        #     'download_info': {
                        #         'sha': result['sha'],
                        #         'path': result['path'],
                        #         'downloaded_at': time.time()
                        #     }
                        # }
                        
                        # metadata_path = workflow_save_path.replace('.yml', '_metadata.json')
                        # with open(metadata_path, 'w', encoding='utf-8') as f:
                        #     json.dump(metadata, f, indent=2, ensure_ascii=False)
                        
                        downloaded_count += 1
                        logger.debug(f"Downloaded successfully: {owner}/{repo}/{workflow_path}")
                        
                    except Exception as e:
                        logger.error(f"Failed to save workflow file: {owner}/{repo}/{workflow_path}: {e}")
                        failed_count += 1
                else:
                    failed_count += 1
            
            logger.success(f"Finished downloading {owner}/{repo}: {downloaded_count} succeeded, {failed_count} failed")
            
        except Exception as e:
            logger.error(f"Error downloading repo workflows: {repo_info_file}: {e}")


def parallel_download_workflows(workflow_info_dir='results/workflow_info', 
                              save_path='results/workflows', 
                              max_workers=8):
    """Download workflows for all repos concurrently."""
    
    # Initialize token manager
    # token_manager = TokenManager(GITHUB_TOKENS)
    downloader = GitHubWorkflowDownloader()
    
    # Collect all workflow info files
    workflow_info_files = []
    for file in os.listdir(workflow_info_dir):
        if file.endswith('#workflows.json'):
            workflow_info_files.append(os.path.join(workflow_info_dir, file))
    
    if not workflow_info_files:
        logger.warning(f"No workflow info files found in {workflow_info_dir}")
        return
    
    logger.info(f"Found workflow info for {len(workflow_info_files)} repos, starting concurrent download...")
    
    # Concurrent download
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(downloader.download_repo_workflows, file_path, save_path) 
                  for file_path in workflow_info_files]
        
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"[Concurrent task error] {e}")


if __name__ == '__main__':
    try:
        # Set worker count
        max_workers = 8
        
        # Start concurrent download
        parallel_download_workflows(
            workflow_info_dir='results/workflow_info',
            save_path='results/workflows',
            max_workers=max_workers
        )
        
        logger.info("All workflow downloads completed!")
        
    except KeyboardInterrupt:
        logger.warning("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error during execution: {e}")
    finally:
        logger.info("Execution finished")