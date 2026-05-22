# Run with: python -m get_info.whole_download_process

import os
import json
from pathlib import Path
from loguru import logger
from config import GITHUB_TOKENS

from utils.utils import TokenManager, pair_workflow_file_and_id
# from utils.RepoCloner import RepoCloner

from get_info.get_workflow_info_of_repos import parallel_get_workflow_info
from get_info.download_workflow import parallel_download_workflows
from get_info.get_workflow_run_records_by_ids import parallel_get_workflow_runs_by_ids
from get_info.unzip_workflow_detail_logs import unzip_workflow_detail_logs
from get_info.download_workflow_logs import detail_workflow_logs_download
from get_info.split_failure_reason import get_failed_runs, split_failure_reason_parallel


def whole_download_process(repos, token_manager, workers, output_dir='results'):

    # Fetch workflow info for all repos (main goal: obtain workflow IDs)
    parallel_get_workflow_info(repos, token_manager, workers, save_path=f'{output_dir}/workflow_info')

    # Download all workflow files
    parallel_download_workflows(
        workflow_info_dir=f'{output_dir}/workflow_info',
        save_path=f'{output_dir}/workflows',
        max_workers=workers
    )

    # Pair local workflows that use docker/build-push-action with workflow IDs
    workflow_id_pair = pair_workflow_file_and_id(
        workflow_info_dir=f'{output_dir}/workflow_info',
        root_dir=f'{output_dir}/workflows',
        output_json=f'{output_dir}/workflow_id_pair.json',
        mode='workflow'
    )

    # Download run summary records for matched docker/build-push-action workflows
    parallel_get_workflow_runs_by_ids(workflow_id_pair, f'{output_dir}/workflow_runs', token_manager, workers)

    # Download detailed workflow run logs
    detail_workflow_logs_download(token_manager, f'{output_dir}/workflow_runs', f'{output_dir}/workflow_logs', workers)

    # Unzip workflow run log archives
    unzip_workflow_detail_logs(f'{output_dir}/workflow_logs', f'{output_dir}/unzipped_workflow_logs', workers)

    # Collect failed run records
    get_failed_runs(f'{output_dir}/workflow_runs', f'{output_dir}/unzipped_workflow_logs', f'{output_dir}/failed_runs.json')

    # Parse detailed logs and extract failure reasons
    split_failure_reason_parallel(
        input_file=f'{output_dir}/failed_runs.json',
        results_dir=f'{output_dir}/unzipped_workflow_logs',
        output_dir=f'{output_dir}/failed_job_logs',
        max_workers=workers
    )

    # Clone required repos locally
    clone_repos = set()
    for folder in os.listdir(f'{output_dir}/failed_job_logs'):
        repo_owner, repo_name = folder.split('#')[:2]
        url = f'https://github.com/{repo_owner}/{repo_name}'
        if url in clone_repos:
            continue
        clone_repos.add(url)
    clone_repos = list(clone_repos)
    logger.info(f'Repos to clone: {len(clone_repos)}')
    
    # cloner = RepoCloner(
    #     output_dir=f'{output_dir}/cloned_repos',
    #     max_workers=workers,
    #     proxy=config.GITHUB_PROXY  # optional GitHub mirror prefix
    # )
    # cloner.clone_repos(clone_repos)


if __name__ == "__main__":
    token_manager = TokenManager(GITHUB_TOKENS)
    workers = 8

    mode = 'single'
    match mode:
        case 'test':
            repos = ['https://github.com/1Panel-dev/KubePi', 'https://github.com/gpustack/gpustack'] # , 'https://github.com/daeuniverse/dae']
            output_dir = 'test_results'
        case 'single':
            repo_file = 'results/500+_repos.json'
            data = json.load(open(repo_file, encoding='utf-8'))
            repos = [repo['url'] for repo in data['repos']]
            output_dir = 'results'
        case 'multiple':
            multiple_repo_files = 'results/crawled_repo_lists/final_results.json'
            repos = set()
            with open(multiple_repo_files, encoding='utf-8') as f:
                data = json.load(f)
            for _, repo_files in data.items():
                for repo_file in repo_files:
                    data = json.load(open(repo_file, encoding='utf-8'))
                    repos.update([repo['url'] for repo in data['repos']])
            repos = list(repos)
            output_dir = 'results_multiple'
        case _:
            raise ValueError(f'Invalid mode: {mode}')

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    logger.info(f'Total repos: {len(repos)}')
    log_path = f'{output_dir}/whole_download.log'

    logger.add(log_path, rotation="1 GB") # , level="INFO")
    logger.info(f'Init results dir: {output_dir}, log file: {log_path}.')
    
    whole_download_process(repos, token_manager, workers, output_dir)
