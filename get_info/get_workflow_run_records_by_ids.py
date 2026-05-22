import json
import os
import requests
import time
from loguru import logger

import concurrent.futures

from utils.GithubRepo import GithubRepo
from utils.utils import check_disk_space, TokenManager, pair_workflow_file_and_id
from config import GITHUB_TOKENS
# Run with: python -m get_info.get_workflow_run_records_by_ids

def is_repo_done(save_file_path, wf_ids, max_runs=10000):
    if not os.path.exists(save_file_path):
        return False
    try:
        with open(save_file_path, encoding='utf-8') as f:
            data = json.load(f)
        for wf_id in wf_ids:
            wf_data = data.get(str(wf_id)) or data.get(wf_id)
            if not wf_data:
                return False
            runs = wf_data.get('runs', [])
            total_count = wf_data.get('total_count', 0)
            if len(runs) >= max_runs or len(runs) == total_count:
                continue
            else:
                return False
        return True
    except Exception as e:
        print(f'[RECOVER] Failed to check {save_file_path}: {e}')
        return False

def handle_rate_limit(response):
    try:
        remaining = int(response.headers.get('X-RateLimit-Remaining', 1))
        reset = int(response.headers.get('X-RateLimit-Reset', 0))
    except Exception:
        remaining = 1
        reset = 0
    if remaining <= 1:
        wait_time = max(reset - int(time.time()), 0) + 5  # extra 5s buffer
        print(f'[RATE LIMIT] Approaching rate limit, sleeping for {wait_time}s until reset...')
        time.sleep(wait_time)
    return remaining

def get_workflow_runs_by_ids(github_token: str, save_path_dir: str, repo: GithubRepo, wf_ids: list[int], 
                             per_page: int = 100, max_runs: int | None = None,
                             max_retries: int = 3) -> dict:
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    all_runs = {}
    save_file_path = os.path.join(save_path_dir, f"{repo.file_name}#runs.json")
    for wf_id in wf_ids:
        all_runs[wf_id] = {'runs': [], 'total_count': 0}
        created_before = None
        fetched = 0
        while True:
            page = 1
            while True:
                params = {
                    'per_page': per_page,
                    'page': page,
                    'status': 'completed'
                }
                if created_before:
                    params['created'] = f'<{created_before}'
                url = f"https://api.github.com/repos/{repo.url_name}/actions/workflows/{wf_id}/runs"
                response = None
                for attempt in range(1, max_retries + 1):
                    try:
                        response = requests.get(url, headers=headers, params=params, timeout=10)
                        if response.status_code == 403 and 'X-RateLimit-Reset' in response.headers:
                            reset = int(response.headers.get('X-RateLimit-Reset', 0))
                            wait_time = max(reset - int(time.time()), 0) + 5
                            logger.warning(f'[RATE LIMIT] Hit rate limit, sleeping for {wait_time}s until reset...')
                            time.sleep(wait_time)
                            continue  # retry
                        response.raise_for_status()
                        handle_rate_limit(response)
                        break  # success, exit retry loop
                    except requests.RequestException as e:
                        logger.error(f"[ERROR] Network or HTTP error for {repo.url_name} (workflow_id={wf_id}), attempt {attempt}: {e}")
                        if attempt < max_retries:
                            wait_time = 2 ** (attempt - 1)
                            logger.info(f"[INFO] Retrying in {wait_time}s...")
                            time.sleep(wait_time)
                        else:
                            logger.error(f"[ERROR] Max retries reached for {repo.url_name} (workflow_id={wf_id}), skipping.")
                            response = None
                if response is None:
                    break
                try:
                    data = response.json()
                except Exception as e:
                    logger.error(f"[ERROR] Failed to parse JSON for {repo.url_name} (workflow_id={wf_id}): {e}")
                    break
                runs = data.get('workflow_runs', [])
                if not runs:
                    break
                all_runs[wf_id]['runs'].extend(runs)
                all_runs[wf_id]['total_count'] = data.get('total_count', 0)
                fetched += len(runs)
                logger.info(f"{repo.url_name} - {wf_id} - Page {page} (created_before={created_before}): "
                            f"{len(runs)} runs fetched, total so far {len(all_runs[wf_id]['runs'])}. "
                            f"{all_runs[wf_id]['total_count']} total count.")
                if max_runs is not None and len(all_runs[wf_id]['runs']) >= max_runs:
                    logger.info(f"{repo.url_name} - {wf_id} - Max runs reached, skipping.")
                    break
                if len(runs) < per_page:
                    break
                page += 1
            # Paginate in segments: if this batch hit 1000 runs, continue with earlier created_at
            if not all_runs[wf_id]['runs']:
                break
            # Find earliest created_at among fetched runs
            earliest = min(run['created_at'] for run in all_runs[wf_id]['runs'])
            # Fewer than 1000 runs in this batch means we are done
            if fetched < 1000:
                break
            # Stop if max_runs reached
            if max_runs is not None and len(all_runs[wf_id]['runs']) >= max_runs:
                break
            created_before = earliest
            fetched = 0  # reset counter for next pagination round
    try:
        os.makedirs(os.path.dirname(save_file_path), exist_ok=True)
        with open(save_file_path, 'w', encoding='utf-8') as f:
            json.dump(all_runs, f, indent=4)
    except Exception as e:
        logger.error(f"[ERROR] Failed to save file {save_file_path}: {e}")
    return all_runs

def parallel_get_workflow_runs_by_ids(repo_and_workflow_ids, save_path_dir, token_manager, max_workers=8, per_page=100, max_runs=10000):
    def task(repo_name, pair):
        repo = GithubRepo(repo_name)
        os.makedirs(save_path_dir, exist_ok=True)
        save_file_path = os.path.join(save_path_dir, f"{repo.file_name}#runs.json")
        if not check_disk_space(save_path_dir):
            return
        if is_repo_done(save_file_path, pair['matched_workflow_ids'], max_runs=max_runs):
            logger.info(f"[RECOVER] Skipping: {repo.url_name}.")
            return
        token = token_manager.get_token()
        try:
            get_workflow_runs_by_ids(token, save_path_dir, repo, pair['matched_workflow_ids'], per_page=per_page, max_runs=max_runs)
        except Exception as e:
            logger.error(f"[ERROR] Exception in get_workflow_runs_by_ids for {repo.url_name}: {e}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(task, repo_name, pair) for repo_name, pair in repo_and_workflow_ids.items()]
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"[Concurrent task error] {e}")

if __name__ == "__main__":
    repo_and_workflow_ids = pair_workflow_file_and_id("results/workflow_info", "results/cloned_repos")
    save_path_dir = "results/workflow_runs"
    token_manager = TokenManager(GITHUB_TOKENS)
    parallel_get_workflow_runs_by_ids(repo_and_workflow_ids, save_path_dir, token_manager, max_workers=8, per_page=100, max_runs=10000)
