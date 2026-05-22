# Run: find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + && python -m get_dataset.get_diff
# Two push kinds: merge PR (two parents) vs linear

# Input: repo, workflow_id, run_id
# Output: push type and diff payload

from pathlib import Path
import json
import os
import yaml
import re
from typing import Any
import itertools
from loguru import logger
from utils.utils import load_workflow_runs
from utils.GithubRepo import get_clean_repo
import traceback
from tqdm import tqdm

def replace_matrix_tokens(text: Any, matrix_vars: dict[str, Any], github_sha: str) -> Any:
    """
    Replace ${{ matrix.<key> }} placeholders with matrix values.
    Allow spaces inside braces; do not trim outside text.
    Return non-strings unchanged.
    """
    if not isinstance(text, str):
        return text
    result = text
    for key, value in matrix_vars.items():
        pattern = re.compile(r"\$\{\{\s*matrix\." + re.escape(str(key)) + r"\s*\}\}")
        result = pattern.sub(str(value), result)
    result = re.sub(r"\$\{\{\s*github\.sha\s*\}\}", github_sha, result)
    return result

def normalize_build_context(context_value: Any) -> Any:
    """
    Normalize docker/build-push-action context:
    - Replace '{{defaultContext}}:' variants with './'
    - Replace '${{ github.workspace }}' variants with './'
    - Replace GITHUB_WORKSPACE env refs with './'
    """
    if not isinstance(context_value, str):
        return context_value
    s = context_value
    # {{defaultContext}}: -> ./
    s = re.sub(r"\{\{\s*defaultContext\s*\}\}\s*:", "./", s)
    # ${{ github.workspace }} -> ./
    s = re.sub(r"\$\{\{\s*github\.workspace\s*\}\}", "./", s)
    # GITHUB_WORKSPACE -> ./
    s = re.sub(r"\$GITHUB_WORKSPACE\b", "./", s)
    s = re.sub(r"\$\{\s*GITHUB_WORKSPACE\s*\}", "./", s)
    return s

def expand_matrix_combinations(matrix_config: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Expand matrix config into combinations with exclude support
    
    Args:
        matrix_config may include:
            - matrix axes
            - include: extra combinations
            - exclude: patterns to drop
            - fail-fast
            - max-parallel
        
    Returns:
        List of combinations after exclude
        
    Examples:
        # standard matrix + exclude
        {
            'platform': ['linux/amd64', 'linux/arm64'],
            'image': ['local', 'cloud'],
            'exclude': [
                {'platform': 'linux/arm64', 'image': 'cloud'}
            ]
        }
        
        # include + exclude
        {
            'include': [
                {'platform': 'linux/amd64', 'image': 'local'},
                {'platform': 'linux/arm64', 'image': 'cloud'}
            ],
            'exclude': [
                {'platform': 'linux/arm64'}
            ]
        }
    """
    combinations = []
    
    if 'include' in matrix_config:
        # include: use predefined rows
        combinations = matrix_config['include']
    else:
        # else: cartesian product
        matrix_keys = []
        matrix_values = []
        
        for key, values in matrix_config.items():
            if key not in ['fail-fast', 'max-parallel', 'exclude']:
                matrix_keys.append(key)
                matrix_values.append(values)
        
        # emit combinations
        if matrix_keys:
            for combination in itertools.product(*matrix_values):
                combination_dict = dict(zip(matrix_keys, combination, strict=False))
                combinations.append(combination_dict)
    
    # apply exclude
    if 'exclude' in matrix_config:
        exclude_patterns = matrix_config['exclude']
        filtered_combinations = []
        
        for combination in combinations:
            should_exclude = False
            
            for exclude_pattern in exclude_patterns:
                # match exclude pattern
                if all(key in combination and combination[key] == value 
                       for key, value in exclude_pattern.items()):
                    should_exclude = True
                    break
            
            if not should_exclude:
                filtered_combinations.append(combination)
        
        combinations = filtered_combinations
    
    return combinations

def find_docker_build_push_action_contexts(workflow_content: dict[str, Any], github_sha: str) -> list[dict[str, Any]]:
    """
    Find docker/build-push-action contexts in workflow YAML
    
    Args:
        workflow_content: parsed workflow dict
            - matrix axes
            - include rows
            - exclude patterns
            - fail-fast / max-parallel
        
    Returns:
        List of context entries with matrix vars and build parameters
    """
    contexts = []
    
    def search_in_jobs(jobs: dict[str, Any]):
        """Walk jobs/steps and expand matrix."""
        for job_name, job_config in jobs.items():
            if isinstance(job_config, dict) and 'steps' in job_config:
                # matrix strategy?
                matrix_combinations = []
                if 'strategy' in job_config and 'matrix' in job_config['strategy']:
                    matrix_combinations = expand_matrix_combinations(job_config['strategy']['matrix'])
                else:
                    # default empty matrix
                    matrix_combinations = [{}]
                
                # one context per matrix row
                for matrix_vars in matrix_combinations:
                    for step in job_config['steps']:
                        if isinstance(step, dict) and 'docker/build-push-action' in step.get('uses', ''):
                            # step.with
                            step_with = step.get('with', {})

                            # collect build-push-action inputs
                            build_params = {}

                            # core paths
                            build_params['context'] = normalize_build_context(step_with.get('context', '.'))
                            build_params['file'] = normalize_build_context(step_with.get('file', 'Dockerfile'))
                            
                            # build controls
                            build_params['platforms'] = [p.strip() for p in step_with.get('platforms', '').split(',')]
                            build_params['target'] = step_with.get('target')
                            build_params['no-cache'] = step_with.get('no-cache', False)
                            build_params['pull'] = step_with.get('pull', False)
                            build_params['push'] = step_with.get('push', False)
                            build_params['load'] = step_with.get('load', False)
                            
                            # tags/outputs
                            build_params['tags'] = step_with.get('tags')
                            build_params['outputs'] = step_with.get('outputs')
                            
                            # cache
                            build_params['cache-from'] = step_with.get('cache-from')
                            build_params['cache-to'] = step_with.get('cache-to')
                            
                            # build-args
                            build_params['build-args'] = step_with.get('build-args')
                            build_params['build-contexts'] = step_with.get('build-contexts')
                            
                            # network/secrets
                            build_params['network'] = step_with.get('network')
                            build_params['allow'] = step_with.get('allow')
                            build_params['secrets'] = step_with.get('secrets')
                            build_params['secret-envs'] = step_with.get('secret-envs')
                            build_params['secret-files'] = step_with.get('secret-files')
                            build_params['ssh'] = step_with.get('ssh')
                            
                            # labels/annotations
                            build_params['labels'] = step_with.get('labels')
                            build_params['annotations'] = step_with.get('annotations')
                            
                            # attestations
                            build_params['attests'] = step_with.get('attests')
                            build_params['provenance'] = step_with.get('provenance')
                            build_params['sbom'] = step_with.get('sbom')
                            
                            # resource limits
                            build_params['shm-size'] = step_with.get('shm-size')
                            build_params['ulimit'] = step_with.get('ulimit')
                            build_params['cgroup-parent'] = step_with.get('cgroup-parent')
                            
                            # misc
                            build_params['add-hosts'] = step_with.get('add-hosts')
                            build_params['builder'] = step_with.get('builder')
                            build_params['call'] = step_with.get('call')
                            build_params['github-token'] = step_with.get('github-token')
                            
                            # substitute ${{ matrix.* }} in string/list fields
                            for param_name, param_value in build_params.items():
                                if isinstance(param_value, str):
                                    build_params[param_name] = replace_matrix_tokens(param_value, matrix_vars, github_sha).strip()
                                elif isinstance(param_value, list):
                                    # substitute list items
                                    processed_list = []
                                    for item in param_value:
                                        processed_list.append(replace_matrix_tokens(item, matrix_vars, github_sha).strip())
                                    build_params[param_name] = processed_list
                            
                            context_info = {
                                'job': job_name,
                                'step_name': step.get('name', 'Unnamed step'),
                                'context': build_params['context'],
                                'file': build_params['file'],
                                'matrix_vars': matrix_vars,
                                'build_parameters': build_params,  # all build inputs
                                'full_step': step
                            }
                            contexts.append(context_info)
    
    # require jobs
    if 'jobs' in workflow_content:
        search_in_jobs(workflow_content['jobs'])
    
    return contexts

def get_diff(cloned_repos, workflow_runs, run_folder, workflow_id_pair):
    owner, repo_name, workflow_id, run_id = run_folder.split('#')
    logger.info(f'Getting diff for {owner}#{repo_name}#{workflow_id}#{run_id}')
    diff = None
    commit_id = None
    message = None
    all_contexts = []  # collected contexts
    
    def return_none(current_branch=None):
        if current_branch is not None:
            repo.git.checkout(current_branch)
        return None, None, None, None
    try:
        for run in workflow_runs[f'{owner}#{repo_name}'][workflow_id]['runs']:
            if str(run['id']) == run_id:
                commit_id = str(run['head_sha'])
                message = str(run['head_commit']['message'])
                break
    except Exception as e:
        logger.error(f'Error loading workflow runs: {e}')
        return return_none()
    repo_path = Path(cloned_repos, f'{owner}#{repo_name}')
    if not repo_path.exists():
        logger.error(f'Repository {repo_path} does not exist')
        return return_none()
    repo = get_clean_repo(repo_path)
    # remember active branch
    try:
        current_branch = repo.active_branch
    except Exception as e:
        logger.error(f'Error getting active branch: {e}')
        return return_none(current_branch)

    try:
        commit = repo.commit(commit_id)
        # checkout to the parent commit to get the unchanged dockerfile
        repo.git.checkout(commit.parents[0])
    except Exception as e:
        logger.error(f'Error getting commit: {e}')
        return return_none(current_branch)

    try:
        workflow_path = None
        index = 0
        for workflow in workflow_id_pair[f'{owner}#{repo_name}']['matched_workflow_ids']:
            if workflow_id == str(workflow):
                workflow_path = workflow_id_pair[f'{owner}#{repo_name}']['workflow_path'][index]
                break
            index += 1
        if workflow_path is None:
            raise ValueError(f'Workflow path not found for {owner}#{repo_name}#{workflow_id}')

        # load workflow YAML
        with open(Path(cloned_repos, f'{owner}#{repo_name}', workflow_path)) as f:
            workflow_content = yaml.safe_load(f)
            contexts = find_docker_build_push_action_contexts(workflow_content, str(commit_id))
            
            if len(contexts) == 0:
                raise ValueError(f'{owner}#{repo_name} has no docker/build-push-action contexts')
            
            logger.info(f'{owner}#{repo_name} has {len(contexts)} docker/build-push-action contexts')
            
            # keep contexts
            all_contexts = contexts
            for context in contexts:
                context['dockerfile_content'] = None
                context_path = Path(cloned_repos, f'{owner}#{repo_name}', context['context'])
                dockerfile = context['file']
                
                # verify paths
                if not context_path.exists():
                    logger.debug(f'Context path does not exist: {context_path}')
                    continue
                
                dockerfile_path = Path(context_path, dockerfile)
                if not dockerfile_path.exists():
                    logger.debug(f'Dockerfile does not exist: {dockerfile_path}')
                    continue

                # read Dockerfile
                with open(dockerfile_path, encoding='utf-8') as f:
                    dockerfile_content = f.read()
                context['dockerfile_content'] = dockerfile_content
    except Exception as e:
        logger.error(f'Error getting dockerfile content: {e}, {traceback.format_exc()}')

    # compute git diff
    if len(commit.parents) == 1:
        diff = commit.parents[0].diff(commit, create_patch=True)
    elif len(commit.parents) == 2:
        # parent[0] is the base branch, parent[1] is the last commit from pr branch
        diff = commit.parents[0].diff(commit.parents[1], create_patch=True)
    # restore branch
    repo.git.checkout(current_branch)
    return commit_id, diff, message, all_contexts

def save_diff(commit_id, diff, message, all_contexts, output_dir, run_folder):
    os.makedirs(output_dir, exist_ok=True)
    
    # per-file diff map
    diff_by_file = {}
    
    # Handle DiffIndex objects (which contain multiple Diff objects)
    if hasattr(diff, '__iter__') and not isinstance(diff, str | bytes):
        # This is likely a DiffIndex or similar iterable object
        for diff_obj in diff:
            if hasattr(diff_obj, 'a_path') and diff_obj.a_path or hasattr(diff_obj, 'b_path') and diff_obj.b_path:
                # file path key
                file_path = diff_obj.a_path if hasattr(diff_obj, 'a_path') and diff_obj.a_path else diff_obj.b_path
                
                # patch text
                if hasattr(diff_obj, 'diff'):
                    diff_content = diff_obj.diff
                    if isinstance(diff_content, bytes):
                        diff_content = diff_content.decode('utf-8', errors='replace')
                    else:
                        diff_content = str(diff_content)
                else:
                    diff_content = str(diff_obj)

                # added/modified/deleted
                file_status = 'unknown'
                if hasattr(diff_obj, 'new_file'):
                    if diff_obj.new_file:
                        file_status = 'added'
                    elif diff_obj.deleted_file:
                        file_status = 'deleted'
                    else:
                        file_status = 'modified'
                
                # store entry
                diff_by_file[file_path] = {
                    'diff_content': diff_content,
                    'file_status': file_status,
                    'old_path': getattr(diff_obj, 'a_path', None),
                    'new_path': getattr(diff_obj, 'b_path', None)
                }
            else:
                # diff without path
                diff_content = str(diff_obj)
                diff_by_file[f'unknown_file_{len(diff_by_file)}'] = {
                    'diff_content': diff_content,
                    'file_status': 'unknown',
                    'old_path': None,
                    'new_path': None
                }
    
    elif hasattr(diff, 'diff'):
        # Single Diff object
        diff_content = diff.diff
        if isinstance(diff_content, bytes):
            diff_content = diff_content.decode('utf-8', errors='replace')
        else:
            diff_content = str(diff_content)
        
        file_path = getattr(diff, 'a_path', 'unknown_file')
        diff_by_file[file_path] = {
            'diff_content': diff_content,
            'file_status': 'modified',
            'old_path': getattr(diff, 'a_path', None),
            'new_path': getattr(diff, 'b_path', None)
        }
    else:
        # Fallback: convert the entire diff object to string
        diff_content = str(diff)
        diff_by_file['unknown_file'] = {
            'diff_content': diff_content,
            'file_status': 'unknown',
            'old_path': None,
            'new_path': None
        }
    
    # bundle metadata
    diff_info = {
        'run_folder': run_folder,
        'commit_id': commit_id,
        'commit_message': message,
        'total_files_changed': len(diff_by_file),
        'diff_files': diff_by_file,
        'docker_build_contexts': all_contexts,
        'context_count': len(all_contexts) if all_contexts else 0
    }
    
    # write JSON
    output_file = Path(output_dir, f'{run_folder}.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(diff_info, f, ensure_ascii=False, indent=2)
    
    logger.success(f'Saved diff for {run_folder} with {len(diff_by_file)} files and {len(all_contexts) if all_contexts else 0} contexts to {output_file}')

def save_diff_dataset(cloned_repos, workflow_run_dir, all_run_folders, output_dir, workflow_id_pair):
    workflow_runs = load_workflow_runs(workflow_run_dir)
    with open(workflow_id_pair, encoding='utf-8') as f:
        workflow_id_pair = json.load(f)

    for run_folder in tqdm(os.listdir(all_run_folders), desc="Processing run folders", total=len(os.listdir(all_run_folders))):
        commit_id, diff, message, all_contexts = get_diff(cloned_repos, workflow_runs, run_folder, workflow_id_pair)
        if commit_id is not None:
            save_diff(commit_id, diff, message, all_contexts, output_dir, run_folder)
        else:
            logger.debug(f'Diff for {run_folder} is None')

if __name__ == '__main__':
    from datetime import datetime
    from config import RESULTS_MULTIPLE_DIR
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    RESULTS_ROOT = str(RESULTS_MULTIPLE_DIR)
    cloned_repos = f'{RESULTS_ROOT}/cloned_repos'
    workflow_run_dir = f'{RESULTS_ROOT}/workflow_runs'
    all_run_folders = f'{RESULTS_ROOT}/failed_job_logs'
    workflow_id_pair = f'{RESULTS_ROOT}/workflow_id_pair.json'

    output_dir = f'{RESULTS_ROOT}/failed_job_diffs_fixed'

    os.makedirs('logs', exist_ok=True)
    logger.add(f'logs/get_diff_results_multiple_{timestamp}.log')
    logger.info(f'Creating diff dataset for {cloned_repos} in {all_run_folders} and saving to {output_dir}')

    try:
        save_diff_dataset(cloned_repos, workflow_run_dir, all_run_folders, output_dir, workflow_id_pair)
        logger.success(f'Diff dataset created for {cloned_repos} in {all_run_folders} and saved to {output_dir}')
    except Exception as e:
        logger.error(f'Error creating diff dataset: {e}')
        raise
