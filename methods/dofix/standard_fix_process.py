# Usage: python -m methods.dofix.standard_fix_process [--project-root DIR] [--dataset-path PATH] ...
# Default project root, dataset, clone dir, results dir, and model name come from config (see config/__init__.py).
import argparse
import os
import json
from loguru import logger
import concurrent.futures
from datetime import datetime
from utils.GithubRepo import get_clean_repo
from methods.dofix.dofix import fix_dockerfile_dofix
import tiktoken
from pathlib import Path
import subprocess
from utils.openai import call_model
from utils.check_repos import check_repos_status
from utils.utils import load_llm_dockerfile_response, num_gpt_tokens_in_a_string
import traceback
from tqdm import tqdm
from config import (
    CLONED_REPOS_DIR,
    DEFAULT_DATASET_PATH,
    LLM_MODEL,
    LLM_API_BASE,
    LLM_API_KEY,
    PROJECT_ROOT,
    RESULTS_DIR,
    results_fixed_dockerfiles_dir,
    results_repairing_dockerfile_dir,
    slug_model,
)

encoding = tiktoken.get_encoding("gpt2")


def write_json_to_file(data, file_path):
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)

class StandardFixProcess:
    def __init__(self, 
        dataset_path: str,
        fixed_dockerfile_base_path: str,
        repo_root: str,
        run_logs_dir: str,
        max_workers: int = 8,
        method: str = "dofix",
        mode: str = "standard",
        model: str = LLM_MODEL,
        api_address: str = LLM_API_BASE,
        api_token: str = LLM_API_KEY):
        
        self.dataset_path = dataset_path
        self.fixed_dockerfile_base_path = fixed_dockerfile_base_path
        self.repo_root = repo_root
        self.api_address = api_address
        self.api_token = api_token
        self.model = model
        self.method = method
        self.mode = mode
        self.dataset = self.load_dataset()
        # selected_dataset = {}
        # for k, v in self.dataset.items():
        #     i = 0
        #     temp = {}
        #     for kk, vv in v.items():
        #         temp[kk] = vv
        #         i += 1
        #         if i >= 5:
        #             break
        #     selected_dataset[k] = temp
        # self.dataset = selected_dataset
        # for debug
        total_full_build_ids = 0
        for v in self.dataset.values():
            total_full_build_ids += len(v)
        logger.info(f"Total full build ids in the dataset: {total_full_build_ids}")

        self.run_logs_dir = run_logs_dir
        self.max_workers = max_workers
        os.makedirs(self.run_logs_dir, exist_ok=True)

    def load_dataset(self):
        with open(self.dataset_path, encoding='utf-8') as f:
            dataset = json.load(f)
        logger.info("Loaded dataset from {} | repos={}", self.dataset_path, len(dataset))
        return dataset
    
    
    def process_repos(self):
        # Run repair flow concurrently at the repo level
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_repo = {
                executor.submit(self.process_repo, repo_key, build_infos): repo_key
                for repo_key, build_infos in tqdm(self.dataset.items(), desc="Processing repos")
            }
            for future in concurrent.futures.as_completed(future_to_repo):
                repo_key = future_to_repo[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Processing repo failed: {repo_key}: {e}, {traceback.format_exc()}")

    def process_repo(self, repo_key, build_infos):
        logger.info(f"Processing repo: {repo_key}")

        run_log_path = os.path.join(self.run_logs_dir, f'repair_results_{self.method}', f"{repo_key}.json")
        prompt_log_path = os.path.join(self.run_logs_dir, f'prompt_results_{self.method}')
        repo_path = os.path.join(self.repo_root, repo_key)

        os.makedirs(os.path.join(self.run_logs_dir, f'repair_results_{self.method}'), exist_ok=True)
        os.makedirs(os.path.join(prompt_log_path), exist_ok=True)

        if os.path.exists(run_log_path):
            results = json.load(open(run_log_path, encoding='utf-8'))
        else:
            results = {}
        for full_build_id, repair_info in list(results.items()):
            repair_status = repair_info[f"{full_build_id}#{self.method}#{model}".replace('/', '_')]['status']
            if repair_status == 'success':
                continue
            elif repair_status == 'failed':
                logger.info(f"{full_build_id} is failed on previous runs, retry.")
                results.pop(full_build_id)

        git_repo = get_clean_repo(repo_path)
        if git_repo is None:
            logger.error(f"Repo not found: {repo_path}")
            return
        
        original_branch = git_repo.active_branch

        logger.info(f"Initialized repo: {repo_key}, original branch: {original_branch}")
        
        for full_build_id, build_info in build_infos.items():
            logger.info(f"Processing build: {full_build_id}")
            build_params = build_info.get('build_params', {})
            if full_build_id not in results:
                results[full_build_id] = {}
            commit_id = build_info.get('head_sha')
            error_log = build_info.get('error_log')
            diff = build_info.get('diff_info')

            context_path = build_params.get('context', '.')
            if context_path.startswith('http') or context_path.startswith('git@') or '***' in context_path:
                context_path = '.'

            if not os.path.isabs(context_path):
                # Relative path, relative to repo root
                context_path = Path(repo_path).joinpath(context_path)

            dockerfile = build_params.get('file', 'Dockerfile')
            if dockerfile.startswith('/') or '***' in dockerfile:
                dockerfile = dockerfile.split('/')[-1]
            dockerfile_path = Path(context_path).joinpath(dockerfile)
            
            try:
                repaired_file_name = f"{full_build_id}#{self.method}#{model}".replace('/', '_')
                if full_build_id in results and repaired_file_name in results.get(full_build_id, {}):
                    logger.info(f"Build already processed: {full_build_id}")
                    continue
                repaired_file_path = Path(self.fixed_dockerfile_base_path).joinpath(repaired_file_name)
                if repaired_file_path.exists():
                    logger.info(f"Repaired Dockerfile already exists: {repaired_file_path}")
                    results[full_build_id][repaired_file_name] = {
                            'status': 'success',
                            'repaired_dockerfile': str(repaired_file_path)
                        }
                    write_json_to_file(results, run_log_path)
                    continue
                
                git_repo.git.checkout(commit_id)
                logger.debug(f"Checked out commit of {full_build_id}: {commit_id}")

                if not Path(context_path).exists():
                    logger.error(f"Context path not found: {context_path}")
                    continue
                if not Path(dockerfile_path).exists():
                    logger.error(f"Dockerfile not found: {dockerfile_path}")
                    continue
                if diff is None:
                    logger.error(f"Diff not found: {full_build_id}")
                    results[full_build_id][repaired_file_name] = {
                        'status': 'failed',
                        'error': 'diff not found'
                    }
                    write_json_to_file(results, run_log_path)
                    continue

                logger.info(f"Fixing build with {model}: {full_build_id}, context_path: {context_path}, dockerfile_path: {dockerfile_path}")
                if self.method == "dofix":
                    # client = init_client(note=f"init client for dofix_{model}")
                    dofix_result = fix_dockerfile_dofix(
                        repo_key, context_path, dockerfile_path, error_log, diff, full_build_id, model, mode=self.mode, dockerfile_content=build_info.get('dockerfile_content', '') # , client
                    )
                    if dofix_result.get('repaired_dockerfile') is not None:
                        with open(repaired_file_path, 'w', encoding='utf-8') as f:
                            f.write(dofix_result['repaired_dockerfile'])
                        
                        # Save prompt/response messages for debugging
                        with open(os.path.join(prompt_log_path, f"{repaired_file_name}.log"), 'w', encoding='utf-8') as f:
                            f.write(dofix_result.get('key_files_prompt', 'EMPTY'))
                            f.write(dofix_result.get('fix_prompt', 'EMPTY'))

                        results[full_build_id][repaired_file_name] = {
                            'repaired_dockerfile': dofix_result['repaired_dockerfile'],
                            'usage': dofix_result['usage'],
                            'key_files_response': dofix_result['key_files_response'],
                            'code_changes': dofix_result['code_changes'],
                            'status': 'success'
                        }
                        write_json_to_file(results, run_log_path)
                        logger.success(f"Repaired Dockerfile: {full_build_id}#{self.method}#{model} with usage: {dofix_result['usage']}")

                    else:
                        logger.error(f"Failed to repair: {full_build_id}#{self.method}#{model}")
                        results[full_build_id][repaired_file_name] = {
                            'status': 'failed',
                            'error': 'failed to repair'
                        }
                        write_json_to_file(results, run_log_path)
                elif self.method == "parfum":
                    command = f'docker-parfum repair --stdin {dockerfile_path} -o {repaired_file_path}'
                    subprocess.run(command, shell=True)
                    results[full_build_id][repaired_file_name] = {
                        'repaired_dockerfile': str(repaired_file_path),
                        'status': 'success'
                    }
                    write_json_to_file(results, run_log_path)
                    logger.success(f"Repaired Dockerfile: {full_build_id}#{self.method}#{model}")
                elif self.method == "pure-llm":
                    dockerfile_content = build_info.get('dockerfile_content', '')
                    prompt = f"""You are a helpful assistant that can repair dockerfiles.
The dockerfile is:
{dockerfile_content}
The error log is:
{error_log}
The diff is:
{diff}
Please repair the dockerfile based on the error log and diff, and return the repaired dockerfile.
Return only the repaired dockerfile with ```Dockerfile ...CONTENT... ``` surrounding."""
                    input_num_tokens = -1
                    try:
                        input_num_tokens = num_gpt_tokens_in_a_string(prompt)
                    except Exception:
                        results[full_build_id][repaired_file_name] = {
                            'input_num_tokens': -1
                        }
                    logger.info(f"Input tokens: {input_num_tokens} for {full_build_id}#{model}")
                    results[full_build_id][repaired_file_name] = {
                        'input_num_tokens': input_num_tokens
                    }
                    write_json_to_file(results, run_log_path)
                    result = call_model(token=self.api_token, api_address=self.api_address, model=model, message=prompt, temperature=0.0, base_delay=10.0)
                    if result['exceed']:
                        logger.warning(f"Error: exceed max tokens for {full_build_id}#{model}")
                        results[full_build_id][repaired_file_name] = {
                            'status': 'failed',
                            'error': result.get('error_type') or 'exceed max tokens',
                            'error_message': result.get('error_message')
                        }
                        write_json_to_file(results, run_log_path)
                    elif result['content'] is None:
                        error_type = result.get('error_type') or 'empty_content'
                        error_message = result.get('error_message') or 'cannot get result content'
                        logger.warning(f"Error: {error_type} for {full_build_id}#{model}: {error_message}")
                        results[full_build_id][repaired_file_name] = {
                            'status': 'failed',
                            'error': error_type,
                            'error_message': error_message
                        }
                        write_json_to_file(results, run_log_path)
                    else:
                        response = load_llm_dockerfile_response(result['content'])
                        if response is not None:
                            results[full_build_id][repaired_file_name] = {
                                'repaired_dockerfile': response,
                                'usage': result['usage'],
                                'status': 'success'
                            }
                            write_json_to_file(results, run_log_path)
                            with open(repaired_file_path, 'w', encoding='utf-8') as f:
                                f.write(response)
                            logger.success(f"Repaired Dockerfile: {full_build_id}#{self.method}#{model}")
                        else:
                            logger.warning(f"Error: cannot load content for {full_build_id}#{model}")
                            results[full_build_id][repaired_file_name] = {
                                'status': 'failed',
                                'error': 'cannot load content',
                                'original_content': result['content']
                            }
                            write_json_to_file(results, run_log_path)

            except Exception as e:
                logger.error(f"Error: {e} at {full_build_id}#{self.method}#{model}, traceback: {traceback.format_exc()}")
                git_repo.git.checkout(original_branch)
                results[full_build_id][repaired_file_name] = {
                    'status': 'failed',
                    'error': str(e)
                }
                write_json_to_file(results, run_log_path)

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-repair Dockerfiles (StandardFixProcess).")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT, help=f"Project root; default {PROJECT_ROOT}.")
    parser.add_argument("--dataset-path", type=Path, default=None, help=f"Dataset JSON; default {DEFAULT_DATASET_PATH} or same filename under <results>.")
    parser.add_argument("--fixed-dockerfile-base-path", type=Path, default=None, help="Output root for repaired Dockerfiles; default results/fixed_dockerfiles/<method>/<mode>/<slug>.")
    parser.add_argument("--repo-root", type=Path, default=None, help="Cloned repos root; default CLONED_REPOS_DIR.")
    parser.add_argument("--run-logs-dir", type=Path, default=None, help="Repair process log root; default results/repairing_dockerfile/<method>/<mode>/<slug>.")
    parser.add_argument("--max-workers", type=int, default=8, help="Thread pool size.")
    parser.add_argument("--api-address", type=str, default=LLM_API_BASE, help="LLM API base URL.")
    parser.add_argument("--api-token", type=str, default=LLM_API_KEY, help="LLM API key.")
    parser.add_argument("--method", default="pure-llm", choices=("dofix", "parfum", "pure-llm"), help="Repair method.")
    parser.add_argument(
        "--mode",
        default="standard",
        choices=(
            "standard",
            "remove_build_channel",
            "remove_key_files",
            "cdg_no_linked",
            "cdg_no_files",
        ),
        help="dofix: standard / ablation; cdg_no_linked=omit cross-instruction linked_commands; cdg_no_files=keep linked, omit affected files list.",
    )
    parser.add_argument("--model", type=str, default=LLM_MODEL, help=f"Model name; default {LLM_MODEL}.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    root = args.project_root.resolve()
    same = root == PROJECT_ROOT.resolve()
    res_dir = RESULTS_DIR if same else root / "results"

    method, mode, model, max_workers, api_address, api_token = args.method, args.mode, args.model, args.max_workers, args.api_address, args.api_token
    slug = slug_model(model)
    dataset_path = args.dataset_path.resolve() if args.dataset_path else (DEFAULT_DATASET_PATH if same else res_dir / DEFAULT_DATASET_PATH.name)
    fixed_base = (
        args.fixed_dockerfile_base_path.resolve()
        if args.fixed_dockerfile_base_path
        else results_fixed_dockerfiles_dir(method, mode, model, results_root=res_dir)
    )
    repo_root = args.repo_root.resolve() if args.repo_root else (CLONED_REPOS_DIR if same else res_dir / CLONED_REPOS_DIR.name)
    run_logs = (
        args.run_logs_dir.resolve()
        if args.run_logs_dir
        else results_repairing_dockerfile_dir(method, mode, model, results_root=res_dir)
    )

    s_run, s_fix, s_ds, s_repo = map(str, (run_logs, fixed_base, dataset_path, repo_root))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.add(f"{s_run}/{method}_{mode}_{slug}_{ts}.log")
    os.makedirs(s_run, exist_ok=True)
    os.makedirs(s_fix, exist_ok=True)

    check_repos_status(str(repo_root))

    logger.info(f"Repair started | dataset={s_ds} | fixed_base={s_fix} | repo={s_repo} | run_logs={s_run} | workers={max_workers} | model={model} | method={method} | mode={mode}")
    StandardFixProcess(s_ds, s_fix, s_repo, s_run, max_workers, method, mode, model, api_address, api_token).process_repos()
    logger.info("Repair process completed.")
