# Run: find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + && python -m get_dataset.get_dataset
"""
Build dataset entries from failed_job_logs.
"""

import os
import json
from pathlib import Path
from typing import Any
from loguru import logger
import traceback
from tqdm import tqdm
from utils.utils import load_workflow_runs
from utils.GithubRepo import get_clean_repo

def parse_folder_name(folder_name: str) -> tuple[str, str, str, str] | None:
    """
    Parse folder name owner#repo#workflow_id#run_id.
    
    Args:
        folder_name: directory name
        
    Returns:
        (owner, repo, workflow_id, run_id) or None
    """
    parts = folder_name.split('#')
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]
    return None


def get_dataset(failed_job_logs_dir: str, workflow_runs_dir: str, diff_dir: str, save_path: str, cloned_repos_dir: str) -> dict[str, Any]:
    """
    Extract build records from failed_job_logs
    
    Args:
        failed_job_logs_dir: failed job logs root
        workflow_runs_dir: workflow runs JSON dir
        diff_dir: diff JSON directory
        save_path: output directory
        cloned_repos_dir: cloned repos root
    Returns:
        dict keyed by build_id with head_sha and build_params
    """

    # 1. load all workflow_runs
    workflow_runs_info = load_workflow_runs(workflow_runs_dir)
    
    # 2. load diff JSON
    diff_path = Path(diff_dir)
    if not diff_path.exists():
        logger.error(f"Diff directory does not exist: {diff_dir}")
        raise ValueError(f"Diff directory does not exist: {diff_dir}")
    diff_files = list(diff_path.glob("*.json"))
    all_diff_info = {}
    for diff_file in tqdm(diff_files, desc="Processing diff files", total=len(diff_files)):
        with open(diff_file, encoding='utf-8') as f:
            diff_info = json.load(f)
            all_diff_info[diff_file.name.replace('.json', '')] = diff_info['diff_files']
    logger.info(f"Loaded {len(all_diff_info)} diff info")
    
    # 3. open git repos
    cloned_repos_path = Path(cloned_repos_dir)
    if not cloned_repos_path.exists():
        logger.error(f"Cloned repos directory does not exist: {cloned_repos_dir}")
        raise ValueError(f"Cloned repos directory does not exist: {cloned_repos_dir}")

    git_repos = {}
    for repo_folder in tqdm(os.listdir(cloned_repos_path), desc="Processing cloned repos", total=len(os.listdir(cloned_repos_path))):
        repo_path = cloned_repos_path / repo_folder
        if not repo_path.is_dir():
            continue
        try:
            repo = get_clean_repo(repo_path)
            if repo is not None:
                git_repos[repo_folder] = repo
            else:
                logger.error(f"Repo not found: {repo_path}")
                continue
        except Exception as e:
            logger.error(f"Error getting clean repo for {repo_folder}: {e}")
            continue

    logger.info(f"Loaded {len(git_repos)} git repos")

    # 4. walk failed_job_logs
    failed_job_logs_path = Path(failed_job_logs_dir)
    if not failed_job_logs_path.exists():
        logger.error(f"Failed job logs directory does not exist: {failed_job_logs_dir}")
        raise ValueError(f"Failed job logs directory does not exist: {failed_job_logs_dir}")

    dataset = {}
    logger.info(f"Processing failed job logs and build params from {failed_job_logs_dir}...")
    for run_folder in tqdm(os.listdir(failed_job_logs_dir), desc="Processing failed job logs", total=len(os.listdir(failed_job_logs_dir))):
        run_folder_path = failed_job_logs_path / run_folder
        if not run_folder_path.is_dir():
            continue
        
        # parse folder name
        parsed = parse_folder_name(run_folder)
        if not parsed:
            logger.warning(f"Failed to parse folder name: {run_folder}")
            continue
        
        owner, repo_name, workflow_id, run_id = parsed
        repo_key = f"{owner}#{repo_name}"
        if repo_key in ["gpustack#gpustack"]: # gpustack builds are too slow
            logger.warning(f"Skipping {repo_key} because it is too slow to build")
            continue
        if repo_key not in dataset:
            dataset[repo_key] = {}
        
        # resolve head_sha from workflow_runs
        head_sha = None
        if repo_key in workflow_runs_info:
            workflow_data = workflow_runs_info[repo_key].get(workflow_id, {})
            runs = workflow_data.get('runs', [])
            for run in runs:
                if str(run.get('id')) == run_id:
                    head_sha = run.get('head_sha')
                    break
        
        if not head_sha:
            logger.warning(f"Head SHA not found for {run_folder}")
            continue

        if repo_key in git_repos:
            repo = git_repos[repo_key]
            try:
                active_branch = repo.active_branch.name
                repo.git.checkout(head_sha)
                repo.git.reset('--hard', 'HEAD')
                repo.git.clean('-xdf')
                repo.git.checkout(active_branch)
            except Exception as e:
                logger.error(f"Error checking out {head_sha} for {repo_key}: {e}")
                continue
        else:
            logger.warning(f"Repo not found: {repo_key}")
            continue

        diff_info = all_diff_info.get(run_folder, None)
        if diff_info is None:
            logger.warning(f"Diff info not found for {run_folder}")
            continue

        # read per-build artifacts
        for fname in os.listdir(run_folder_path):
            fpath = run_folder_path / fname
            build_id = fname.split('#')[-2]
            full_build_id = f"{run_folder}#{build_id}"

            if full_build_id not in dataset[repo_key]:
                dataset[repo_key][full_build_id] = {}
            dataset[repo_key][full_build_id]['head_sha'] = head_sha
            dataset[repo_key][full_build_id]['run_folder'] = str(run_folder_path.absolute())
            dataset[repo_key][full_build_id]['diff_info'] = diff_info

            if not fpath.is_file():
                continue
            if fname.endswith('.txt'):
                dataset[repo_key][full_build_id]['error_log_path'] = str(fpath.absolute())
                dataset[repo_key][full_build_id]['error_log'] = open(fpath, encoding='utf-8', errors='ignore').read()
            elif fname.endswith('.json'):
                dataset[repo_key][full_build_id]['build_params_path'] = str(fpath.absolute())
                dataset[repo_key][full_build_id]['build_params'] = json.load(open(fpath, encoding='utf-8', errors='ignore'))

                build_params = dataset[repo_key][full_build_id]['build_params']
                dataset[repo_key][full_build_id]['dockerfile_content'] = None

                context_path = build_params.get('context', '.')
                if context_path.startswith('http') or context_path.startswith('git@') or '***' in context_path:
                    context_path = '.'
                if not os.path.isabs(context_path):
                    # resolve relative to repo root
                    context_path = Path(f'{cloned_repos_path}/{repo_key}').joinpath(context_path)
                dockerfile = build_params.get('file', 'Dockerfile')
                if dockerfile.startswith('/') or '***' in dockerfile:
                    dockerfile = dockerfile.split('/')[-1]
                dockerfile_path = Path(context_path).joinpath(dockerfile)
                active_branch = repo.active_branch.name
                repo.git.checkout(head_sha)
                try:
                    with open(dockerfile_path, encoding='utf-8') as f:
                        dockerfile_content = f.read()
                        dataset[repo_key][full_build_id]['dockerfile_content'] = dockerfile_content
                        skip_list = ["nvidia/tritonserver", "nvidia/cuda", "FROM pytorch", "install torch", "tensorflow", "torchvision"]
                        if any(skip_keyword in dockerfile_content for skip_keyword in skip_list):
                            logger.warning(f"Skipping {full_build_id} because it contains one of {skip_list}")
                            dataset[repo_key][full_build_id]['big_file'] = True
                        else:
                            dataset[repo_key][full_build_id]['big_file'] = False
                except Exception as e:
                    logger.error(f"Error getting dockerfile content: {e}, {traceback.format_exc()}")
                repo.git.reset('--hard', 'HEAD')
                repo.git.clean('-xdf')
                repo.git.checkout(active_branch)

            logger.success(f"Processed {fname}")
    
    logger.success(f"Extracted build info for {len(dataset)} builds")

    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    logger.success(f"Saved dataset to {save_path}")

    count_buildid = 0
    count_keeped_builds = 0
    count_skipped_builds = 0
    for _, build_infos in dataset.items():
        count_buildid += len(build_infos)
        for _, build_info in build_infos.items():
            if build_info.get('big_file', False):
                count_skipped_builds += 1
            else:
                count_keeped_builds += 1
    logger.success(f"Extracted {count_buildid} builds from {len(dataset)} repositories, "
                   f"{count_keeped_builds} builds can be built, {count_skipped_builds} builds are skipped because they contain too large files")

    return dataset

if __name__ == "__main__":
    from datetime import datetime
    base_dir = "results_multiple"
    failed_job_logs_dir = f"{base_dir}/failed_job_logs_fixed_params"
    workflow_runs_dir = f"{base_dir}/workflow_runs"
    diff_dir = f"{base_dir}/failed_job_diffs_fixed"
    save_path = f"{base_dir}/dataset_multiple_valid_fixed_params_with_dockerfile.json"
    cloned_repos_dir = f"{base_dir}/cloned_repos"

    os.makedirs("logs", exist_ok=True)
    logger.add(f"logs/get_dataset_multiple_fixed_params_with_dockerfile_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    logger.info(f"Loading dataset from {failed_job_logs_dir}, {workflow_runs_dir}, {diff_dir}, {save_path}, {cloned_repos_dir}")
    dataset = get_dataset(failed_job_logs_dir, workflow_runs_dir, diff_dir, save_path, cloned_repos_dir)
