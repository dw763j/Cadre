import os
import re
import json
import shutil
import requests
import threading
from pathlib import Path
from loguru import logger
from tqdm import tqdm
from typing import Any
import tiktoken
import openai

class TokenManager:
    """Token manager with round-robin rotation."""
    
    def __init__(self, tokens: list[str]):
        if not tokens:
            raise ValueError("Please set GITHUB_TOKENS in .env")
        self.tokens = tokens
        self.current_index = 0
        self.lock = threading.Lock()
    
    def get_token(self) -> str:
        """Return the next token in rotation."""
        with self.lock:
            token = self.tokens[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.tokens)
            return token

def init_client(base_url: str, api_key: str):
    client = openai.OpenAI(base_url=base_url, api_key=api_key)
    masked = f"{api_key[:4]}...{api_key[-4:]}" if api_key and len(api_key) > 8 else "***"
    logger.info(f"Using client from {base_url} (api_key={masked})")
    return client

def load_llm_json_response(response:str) -> dict[str, Any] | None:
    """Load LLM JSON response."""
    try:
        response = response.strip().replace('\n', '')
        if response.startswith('```json'):
            response = response[7:].strip()
        else:
            return json.loads(response)
        if response.endswith('```'):
            response = response[:-3].strip()
        return json.loads(response)
    except Exception as e:
        logger.error(f"Error loading LLM JSON response: {e}")
        return None

def load_llm_dockerfile_response(response:str) -> str | None:
    """Load LLM Dockerfile response."""
    try:
        response = response.strip()
        if response.startswith('```Dockerfile') or response.startswith('```dockerfile'):
            response = response[13:-3].strip()
        elif response.startswith('```'):
            response = response[3:-3].strip()
        elif '```Dockerfile' in response or '```dockerfile' in response:
            start = response.index('```Dockerfile') if '```Dockerfile' in response else response.index('```dockerfile')
            end = response.rindex('```')
            response = response[start+13:end].strip()
        elif '```' in response:
            start = response.index('```')
            end = response.rindex('```')
            response = response[start+3:end].strip()
        else:
            return None
        return response
    except Exception as e:
        logger.error(f"Error loading LLM Dockerfile response: {e}")
        return None

def num_gpt_tokens_in_a_string(string: str, encoding_name: str = 'gpt2') -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

def check_disk_space(path, min_bytes=1_000_000_000):
    _, _, free = shutil.disk_usage(path)
    if free < min_bytes:
        logger.warning(f'[DISK] Free space {free} bytes < {min_bytes} bytes, stopping analysis.')
        return False
    return True

def convert_github_url_to_raw(url: str) -> str:
    """
    Convert a GitHub file URL to a raw download URL.

    :param url: GitHub file URL, e.g. https://github.com/owner/repo/blob/branch/path/to/file
    :return: Raw download URL, e.g. https://raw.githubusercontent.com/owner/repo/branch/path/to/file
    """
    if not url or 'github.com' not in url:
        return url

    # Replace github.com with raw.githubusercontent.com
    raw_url = url.replace('github.com', 'raw.githubusercontent.com')

    # Remove /blob/ segment
    raw_url = raw_url.replace('/blob/', '/')

    return raw_url

def download_file(download_url: str, save_path: str = "") -> str:
    """
    Download a file.

    :param download_url: Raw download URL
    :param save_path: Save path; if empty, return file contents instead
    :return: File contents or save path
    """
    try:
        response = requests.get(download_url)
        if response.status_code == 200:
            if save_path:
                with open(save_path, 'w', encoding='utf-8') as f:
                    f.write(response.text)
                return save_path
            else:
                return response.text
        else:
            print(f"Failed to download {download_url}: {response.status_code}")
            return ""
    except Exception as e:
        print(f"Error downloading {download_url}: {e}")
        return ""


def rename_tar_to_zip(folder_path: str) -> list[str]:
    """
    Rename all .tar files in a folder to .zip.

    :param folder_path: Folder path
    :return: List of renamed file paths
    """
    renamed_files = []
    folder = Path(folder_path)
    
    if not folder.exists():
        logger.error(f"Folder does not exist: {folder_path}")
        return renamed_files
    
    if not folder.is_dir():
        logger.error(f"Path is not a directory: {folder_path}")
        return renamed_files
    
    # Find all .tar files
    tar_files = list(folder.glob("*.tar"))
    
    if not tar_files:
        logger.error(f"No .tar files found in {folder_path}")
        return renamed_files
    
    logger.info(f"Found {len(tar_files)} .tar file(s)")
    
    for tar_file in tar_files:
        try:
            # Build new filename (.tar -> .zip)
            new_name = tar_file.stem + ".zip"
            new_path = tar_file.parent / new_name
            
            # Skip if target already exists
            if new_path.exists():
                logger.debug(f"Target file already exists, skipping: {new_name}")
                continue
            
            # Rename file
            tar_file.rename(new_path)
            renamed_files.append(str(new_path))
            
        except Exception as e:
            logger.error(f"Failed to rename {tar_file.name}: {e}")
    
    logger.info(f"Rename complete: {len(renamed_files)}/{len(tar_files)} succeeded")
    return renamed_files

def find_workflows_with_docker_action(root_dir="results/cloned_repos", mode='repo'):  # , output_json="workflow_with_docker.json"):
    """
    Find all workflow files containing uses: docker/build-push-action.
    Returns: {repo_name: [workflow_file_path, ...], ...}
    """
    result = {}
    for repo_name in os.listdir(root_dir):
        logger.debug(repo_name)
        repo_path = Path(root_dir) / repo_name
        if mode == 'repo':
            wf_dir = repo_path / ".github" / "workflows"
        elif mode == 'workflow':
            wf_dir = repo_path 
        if not wf_dir.is_dir():
            continue
        matches = []
        yml_files = list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))
        for yml_file in yml_files:
            logger.debug(yml_file)
            try:
                with open(yml_file, encoding="utf-8") as f:
                    content = f.read()
                if "docker/build-push-action" in content:
                    matches.append(str(yml_file))
            except Exception as e:
                logger.warning(f"[WARN] Failed to parse {yml_file}: {e}")
        if matches:
            repo_workflows = []
            
            for match in matches:
                if mode == 'repo':
                    reg = re.search(r'cloned_repos/([^/]+)/\.github/workflows/([^/]+)$', match)
                    if reg:
                        repo_name = reg.group(1)
                        workflow_file = reg.group(2)
                        workflow_path = f".github/workflows/{workflow_file}"
                        repo_workflows.append(workflow_path)
                elif mode == 'workflow':
                    repo_name = match.split('/')[-2]
                    repo_workflows.append(f".github/workflows/{match.split('/')[-1]}")
            result[repo_name] = repo_workflows
    return result

def pair_workflow_file_and_id(workflow_info_dir="results/workflow_info",root_dir="results/cloned_repos",
                              output_json=None, mode='repo'):
    results = {}
    workflow_with_docker = find_workflows_with_docker_action(root_dir=root_dir, mode=mode)
    for repo_name, workflow_paths in workflow_with_docker.items():
        json_file_path = Path(workflow_info_dir) / f"{repo_name}#workflows.json"
        try:
            with open(json_file_path, encoding='utf-8') as f:
                workflows = json.load(f)
        except Exception as e:
            logger.error(f"[ERROR] Failed to read workflow info: {json_file_path}, {e}")
            continue
        workflow_ids = []
        matched_workflows_paths = []
        for workflow_path in workflow_paths:
            for workflow in workflows:
                if workflow.get('path') == workflow_path:
                    workflow_ids.append(workflow.get('id'))
                    matched_workflows_paths.append(workflow_path)
        unmatched_workflows_paths = list(set(workflow_paths) - set(matched_workflows_paths))
        results[repo_name] = {
            'repo_name': repo_name,
            'workflow_path': [str(workflow_path) for workflow_path in workflow_paths],
            'matched_workflow_ids': list(set(workflow_ids)),
            'unmatched_workflow_paths': list(set(unmatched_workflows_paths)),
            'workflow_info_json_path': str(json_file_path)
        }
    if output_json:
        try:
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            logger.info(f"[INFO] Saved workflow/workflow-id pairing for docker/build-push-action to {output_json}")
        except Exception as e:
            logger.error(f"[ERROR] Failed to save pairing results to {output_json}: {e}")
    return results

def load_workflow_runs(workflow_runs_dir: str) -> dict[str, dict[str, Any]]:
    """
    Load all workflow_runs JSON files into memory at once.

    Args:
        workflow_runs_dir: Path to the workflow_runs directory

    Returns:
        dict: {repo_key: workflow_runs_data}
    """
    workflow_runs_info = {}
    workflow_runs_path = Path(workflow_runs_dir)
    
    if not workflow_runs_path.exists():
        logger.warning(f"Workflow runs directory does not exist: {workflow_runs_dir}")
        return workflow_runs_info
    
    logger.info(f"Loading workflow runs info from {workflow_runs_dir}...")
    workflow_runs_files = list(workflow_runs_path.glob("*.json"))
    for workflow_runs_file in tqdm(workflow_runs_files, desc="Loading workflow runs info", total=len(workflow_runs_files)):
        try:
            repo_key = workflow_runs_file.name.replace('#runs.json', '')
            with open(workflow_runs_file, encoding='utf-8') as f:
                workflow_runs_info[repo_key] = json.load(f)
        except Exception as e:
            logger.error(f"Error loading workflow runs from {workflow_runs_file}: {e}")
            continue
    
    logger.info(f"Loaded workflow runs for {len(workflow_runs_info)} repositories")
    return workflow_runs_info

if __name__ == "__main__":
    from config import CLONED_REPOS_DIR
    pair_workflow_file_and_id('results/workflow_info', str(CLONED_REPOS_DIR), 'results/workflow_id_pair.json')