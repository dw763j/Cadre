import os
import re
import json
from pathlib import Path
from loguru import logger
from tqdm import tqdm
import multiprocessing as mp

FAIL_LOG_DELIM = '------'
FAIL_LOC_DELIM = '--------------------'

def get_failed_runs(runs_dir, logs_dir, output_file):
    log_folders = set(os.listdir(logs_dir))
    failed_runs = {}
    event_failure_counts = {}
    for filename in tqdm(os.listdir(runs_dir)):
        if not filename.endswith('runs.json'):
            continue
        try:
            repo_owner, repo_name, _ = filename.split('#', 2)
        except ValueError:
            continue
        filepath = os.path.join(runs_dir, filename)
        with open(filepath) as f:
            data = json.load(f)
        for workflow_id, wf_data in data.items():
            for run in wf_data.get('runs', []):
                event = run.get('event')
                conclusion = run.get('conclusion')
                run_id = run.get('id')
                head_sha = run.get('head_sha', '')

                folder_name = f"{repo_owner}#{repo_name}#{workflow_id}#{run_id}"
                if conclusion == 'failure' and folder_name in log_folders:
                    if f"{repo_owner}#{repo_name}" not in failed_runs:
                        failed_runs[f"{repo_owner}#{repo_name}"] = []
                    failed_runs[f"{repo_owner}#{repo_name}"].append({"folder_name": folder_name, "head_sha": head_sha, "event": event})

                    if event not in event_failure_counts:
                        event_failure_counts[event] = 0
                    event_failure_counts[event] += 1
    with open(output_file, 'w') as f:
        json.dump({"failed_runs": failed_runs, "event_failure_counts": event_failure_counts}, f, indent=4)

    return failed_runs, event_failure_counts

def get_failed_folders(input_file):
    failed_folders = set()
    with open(input_file) as f:
        data = json.load(f)
        failed_runs = data['failed_runs']
        for _, runs in failed_runs.items():
            for run in runs:
                folder_name = run['folder_name']
                failed_folders.add(folder_name)
    return failed_folders

def extract_docker_build_error_blocks(text):
    lines = [remove_timestamp(line).strip() for line in text.splitlines()]
    in_docker_block = False
    docker_build_command = None
    docker_block = []
    error_blocks = []
    for line in lines:
        if '[command]/usr/bin/docker build ' in line or '[command]/usr/bin/docker buildx build' in line:
            docker_build_command = line
            if in_docker_block and docker_block:
                error_blocks.extend(extract_fail_blocks_from_docker_block('\n'.join(docker_block)))
                docker_block = []
            in_docker_block = True
        # end docker block
        elif (line.startswith('[command]') or line.startswith('##[group]') or \
            line.startswith('##[endgroup]') or line.startswith('##[error]') or \
            line.startswith('##[warning]')) and in_docker_block:
            error_blocks.extend(extract_fail_blocks_from_docker_block('\n'.join(docker_block)))
            docker_block = []
            in_docker_block = False
        # lines inside docker block
        if in_docker_block:
            docker_block.append(line)
    # final block
    if in_docker_block and docker_block:
        error_blocks.extend(extract_fail_blocks_from_docker_block('\n'.join(docker_block)))
    
    # parse docker build CLI flags
    build_params = {}
    if docker_build_command:
        build_params = parse_docker_build_command(docker_build_command)
        build_params['docker_build_command'] = docker_build_command
    
    return error_blocks, build_params

def extract_fail_blocks_from_docker_block(block_text):
    # blocks wrapped in ------
    blocks = block_text.split(FAIL_LOG_DELIM)
    fail_logs = []
    for i in range(1, len(blocks)):
        log = blocks[i].strip()
        # skip cache-registry-only errors
        if is_only_cache_registry_error(log):
            continue
        # skip logs without docker step prefix
        if has_docker_step_prefix(log):
            continue
        fail_logs.append(log)
    return normalize_fail_logs(fail_logs)

def normalize_fail_logs(fail_logs):
    # drop leading empty strings
    while fail_logs and fail_logs[0] == "":
        fail_logs.pop(0)
    # collapse consecutive empties
    normalized_logs = []
    last_was_empty = False
    for log in fail_logs:
        if log == "":
            if not last_was_empty:
                normalized_logs.append("")
            last_was_empty = True
        else:
            normalized_logs.append(log)
            last_was_empty = False
    return normalized_logs

def is_only_cache_registry_error(log):
    """
    Return True if the block only reports remote cache export issues
    """
    # Typical markers: cache registry lines only, no other serious errors
    lines = [line.strip() for line in log.splitlines() if line.strip()]
    if not lines:
        return False
    # every line must be cache-related
    cache_related = False
    for line in lines:
        if (
            'exporting cache to registry' in line.lower() or
            'invalid configuration for remote cache' in line.lower() or
            'importing cache manifest from' in line.lower()
        ):
            cache_related = True
        else:
            # any other line => not cache-only
            return False
    return cache_related

def has_docker_step_prefix(log):
    """
    Return True if any line starts with #<step>
    """
    for line in log.splitlines():
        if re.match(r'^#\d+', line.strip()):
            return True
    return False

def remove_timestamp(line):
    # strip leading ISO timestamps
    return re.sub(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ?', '', line)

def parse_docker_build_command(command_line):
    """
    Parse docker build CLI into build_params like get_diff.py
    
    Args:
        command_line: docker build command, e.g.
        [command]/usr/bin/docker buildx build --iidfile /path/to/file --platform linux/amd64,linux/arm64 --tag image:tag .
    
    Returns:
        dict: build parameters in get_diff.py format
    """
    if not command_line:
        return {}
    
    # strip [command] prefix and timestamp
    command = remove_timestamp(command_line)
    if command.startswith('[command]'):
        command = command[9:].strip()
    
    # strip /usr/bin/docker build[x] prefix
    command = re.sub(r'^/usr/bin/docker\s+(?:buildx\s+)?build\s*', '', command)
    
    build_params = {}
    
    # parse flags
    parsed_args = parse_command_arguments(command)
    
    # map flags to build_params
    # --platform
    if 'platform' in parsed_args:
        platforms_str = parsed_args['platform']
        build_params['platform'] = [p.strip() for p in platforms_str.split(',')]
    
    # --tag
    if 'tag' in parsed_args:
        tag_values = parsed_args['tag'] if isinstance(parsed_args['tag'], list) else [parsed_args['tag']]
        build_params['tag'] = tag_values
    
    # --file
    if 'file' in parsed_args:
        build_params['file'] = parsed_args['file']
    elif 'f' in parsed_args:
        build_params['file'] = parsed_args['f']
    
    # build context (often last arg)
    if 'context' in parsed_args:
        last_part = parsed_args['context']
        # git URL context?
        if last_part.startswith('http') and '#' in last_part:
            # git URL with ref
            # https://github.com/owner/repo.git#ref
            build_params['context'] = last_part
            # logger.debug(f'context is a git url: {last_part}')
        elif not (last_part.endswith('.json') or last_part.endswith('.txt')):
            # path context
            build_params['context'] = last_part
        # skip metadata paths
    
    # --build-arg
    if 'build-arg' in parsed_args:
        build_params['build-arg'] = {}
        build_arg_values = parsed_args['build-arg'] if isinstance(parsed_args['build-arg'], list) else [parsed_args['build-arg']]
        for arg in build_arg_values:
            if '=' in arg:
                key, value = arg.split('=', 1)
                build_params['build-arg'][key] = value
            else:
                build_params['build-arg'][arg] = None
    
    # cache flags
    if 'cache-from' in parsed_args:
        build_params['cache-from'] = parsed_args['cache-from']
    
    if 'cache-to' in parsed_args:
        build_params['cache-to'] = parsed_args['cache-to']
    
    # --target
    if 'target' in parsed_args:
        build_params['target'] = parsed_args['target']
    
    # --push
    if 'push' in parsed_args:
        build_params['push'] = True
    
    # --load
    if 'load' in parsed_args:
        build_params['load'] = True
    
    # --no-cache
    if 'no-cache' in parsed_args:
        build_params['no-cache'] = True
    
    # --pull
    if 'pull' in parsed_args:
        build_params['pull'] = True
    
    # --network
    if 'network' in parsed_args:
        build_params['network'] = parsed_args['network']
    
    # --allow
    if 'allow' in parsed_args:
        allow_values = parsed_args['allow'] if isinstance(parsed_args['allow'], list) else [parsed_args['allow']]
        build_params['allow'] = allow_values
    
    # --tag
    if 'label' in parsed_args:
        build_params['label'] = {}
        label_values = parsed_args['label'] if isinstance(parsed_args['label'], list) else [parsed_args['label']]
        for label in label_values:
            if '=' in label:
                key, value = label.split('=', 1)
                build_params['label'][key] = value
            else:
                build_params['label'][label] = None
    
    # --annotation
    if 'annotation' in parsed_args:
        build_params['annotation'] = {}
        annotation_values = parsed_args['annotation'] if isinstance(parsed_args['annotation'], list) else [parsed_args['annotation']]
        for annotation in annotation_values:
            if '=' in annotation:
                key, value = annotation.split('=', 1)
                build_params['annotation'][key] = value
            else:
                build_params['annotation'][annotation] = None
    
    # --attest
    if 'attest' in parsed_args:
        attest_values = parsed_args['attest'] if isinstance(parsed_args['attest'], list) else [parsed_args['attest']]
        build_params['attest'] = attest_values
    
    # --provenance
    if 'provenance' in parsed_args:
        build_params['provenance'] = parsed_args['provenance']
    
    # --sbom
    if 'sbom' in parsed_args:
        build_params['sbom'] = True
    
    # --builder
    if 'builder' in parsed_args:
        build_params['builder'] = parsed_args['builder']
    
    # --output
    if 'output' in parsed_args:
        output_values = parsed_args['output'] if isinstance(parsed_args['output'], list) else [parsed_args['output']]
        build_params['output'] = output_values
    
    # --metadata-file
    if 'metadata-file' in parsed_args:
        build_params['metadata-file'] = parsed_args['metadata-file']
    
    # --iidfile
    if 'iidfile' in parsed_args:
        build_params['iidfile'] = parsed_args['iidfile']
    
    # --secret
    if 'secret' in parsed_args:
        build_params['secret'] = []
        secret_values = parsed_args['secret'] if isinstance(parsed_args['secret'], list) else [parsed_args['secret']]
        for secret in secret_values:
            if '=' in secret:
                # id=name,src=path
                secret_parts = secret.split(',')
                secret_dict = {}
                for part in secret_parts:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        secret_dict[key] = value
                build_params['secret'].append(secret_dict)
            else:
                build_params['secret'].append(secret)
    
    # other boolean flags
    if 'compress' in parsed_args:
        build_params['compress'] = True
    
    if 'quiet' in parsed_args:
        build_params['quiet'] = True
    
    if 'progress' in parsed_args:
        build_params['progress'] = True
    
    # defaults
    if 'context' not in build_params:
        build_params['context'] = '.'
    
    if 'file' not in build_params:
        build_params['file'] = 'Dockerfile'
    
    return build_params

def parse_command_arguments(command):
    """
    Parse CLI tokens with quoted values and spaces
    
    Args:
        command: args after docker build prefix
    
    Returns:
        dict: flag name to value
    """
    import shlex
    args = {}

    def add_arg(name, value):
        if name in args:
            if not isinstance(args[name], list):
                args[name] = [args[name]]
            args[name].append(value)
        else:
            args[name] = value

    def is_negative_number(token):
        if not token or token[0] != '-':
            return False
        # negative numbers are values
        try:
            float(token)
            return True
        except ValueError:
            return False

    tokens = shlex.split(command)
    i = 0
    last_positional = None
    
    # boolean flags without values
    boolean_flags = {
        'push', 'load', 'no-cache', 'pull', 'compress', 'quiet', 'progress', 
        'sbom', 'provenance', 'help', 'version'
    }
    
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith('--'):
            # long option
            if '=' in tok:
                name, val = tok[2:].split('=', 1)
                add_arg(name, val)
                i += 1
                continue
            name = tok[2:]
            # boolean flag
            if name in boolean_flags:
                add_arg(name, True)
                i += 1
                continue
            # value is next token
            if i + 1 < len(tokens):
                nxt = tokens[i + 1]
                if (not nxt.startswith('-')) or is_negative_number(nxt):
                    add_arg(name, nxt)
                    i += 2
                    continue
            # flag-only
            add_arg(name, True)
            i += 1
        elif tok.startswith('-') and len(tok) == 2 and tok[1].isalpha():
            # short option
            name = tok[1]
            if i + 1 < len(tokens):
                nxt = tokens[i + 1]
                if (not nxt.startswith('-')) or is_negative_number(nxt):
                    add_arg(name, nxt)
                    i += 2
                    continue
            add_arg(name, True)
            i += 1
        else:
            # positional arg
            last_positional = tok
            i += 1

    # last positional as context unless metadata
    # prefer . or path-like tokens
    if last_positional:
        # use trailing path token
        # exclude Chinese punctuation fragments
        if (last_positional == '.' or 
            last_positional.startswith('/') or 
            last_positional.startswith('./') or
            last_positional.startswith('../') or
            (not last_positional.endswith('.json') and 
             not last_positional.endswith('.txt') and
             not any(char in last_positional for char in '\uff0c\u3002\u3001\uff1a\uff1b\u7b49'))):  # fullwidth/CJK punctuation often marks non-path log noise
            args['context'] = last_positional

    return args

def tokenize_command(command):
    """
    Tokenize a command line respecting quotes and parentheses
    
    Args:
        command: command line
    
    Returns:
        list: tokens
    """
    tokens = []
    current_token = ""
    in_quotes = False
    quote_char = None
    paren_depth = 0  # parenthesis depth
    i = 0
    
    while i < len(command):
        char = command[i]
        
        # backslash escape
        if char == '\\' and i + 1 < len(command):
            # consume escaped char
            current_token += command[i + 1]
            i += 2
            continue
        
        if char in ('"', "'"):
            if not in_quotes:
                # open quote
                in_quotes = True
                quote_char = char
                # quotes not kept in token
            elif char == quote_char:
                # close quote
                in_quotes = False
                quote_char = None
                # quotes not kept in token
            else:
                # other quote char inside string
                current_token += char
        elif char == '(' and not in_quotes:
            # open paren
            paren_depth += 1
            current_token += char
        elif char == ')' and not in_quotes:
            # close paren
            paren_depth = max(0, paren_depth - 1)
            current_token += char
        elif char == ' ' and not in_quotes and paren_depth == 0:
            # whitespace delimiter
            if current_token:
                tokens.append(current_token)
                current_token = ""
        else:
            # literal char
            current_token += char
        
        i += 1
    
    # flush last token
    if current_token:
        tokens.append(current_token)
    
    return tokens

def process_single_workflow(args):
    """
    Process one workflow log folder (worker entry)
    
    Args:
        args: (wf_folder, results_dir, output_dir, failed_folders)
    
    Returns:
        tuple: (wf_folder, success_count, total_count)
    """
    wf_folder, results_dir, output_dir, failed_folders = args
    
    if wf_folder not in failed_folders:
        return wf_folder, 0, 0
    
    wf_path = os.path.join(results_dir, wf_folder)
    if not os.path.isdir(wf_path):
        return wf_folder, 0, 0
    
    success_count = 0
    total_count = 0
    index = 0
    
    for fname in os.listdir(wf_path):
        fpath = os.path.join(wf_path, fname)
        if not os.path.isfile(fpath):
            continue
        
        try:
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                text = f.read()
            
            fail_logs, build_params = extract_docker_build_error_blocks(text)
            error_flag = False
            
            for log in fail_logs:
                if ': no space left on device' in log:
                    continue
                if 'ERROR' in log:
                    error_flag = True
            
            if not error_flag:
                continue
            
            if len(fail_logs) > 0:
                total_count += 1
                out_dir = os.path.join(output_dir, wf_folder)
                os.makedirs(out_dir, exist_ok=True)
                
                # write fail_log.txt
                with open(os.path.join(out_dir, f'{wf_folder}#{index}#fail_log.txt'), 'w', encoding='utf-8') as f:
                    for fail_log in fail_logs:
                        f.write(fail_log + '\n')
                
                # write build_params.json
                if build_params:
                    with open(os.path.join(out_dir, f'{wf_folder}#{index}#build_params.json'), 'w', encoding='utf-8') as f:
                        json.dump(build_params, f, indent=2, ensure_ascii=False)
                
                success_count += 1
                index += 1
                
        except Exception as e:
            logger.error(f"Error processing {wf_folder}#{fname}: {e}")
            continue
    
    return wf_folder, success_count, total_count

def split_failure_reason_parallel(input_file, results_dir, output_dir, max_workers=None):
    """
    Parallel split_failure_reason
    
    Args:
        input_file: failed_runs JSON
        results_dir: unzipped workflow logs
        output_dir: parsed failure output
        max_workers: process count (default: CPU cores)
    """
    failed_folders = get_failed_folders(input_file)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    if max_workers is None:
        max_workers = mp.cpu_count()
    
    # folders to process
    workflow_folders = []
    for wf_folder in os.listdir(results_dir):
        if wf_folder in failed_folders: # and wf_folder == "binary-husky#gpt_academic#56499368#14722499660": # debug
            workflow_folders.append(wf_folder)
    
    logger.info(f"Processing {len(workflow_folders)} workflow folders with {max_workers} workers")
    
    # worker args
    process_args = [(wf_folder, results_dir, output_dir, failed_folders) 
                    for wf_folder in workflow_folders]
    
    total_error_files = 0
    total_processed = 0
    
    # process pool
    with mp.Pool(processes=max_workers) as pool:
        # progress bar
        results = list(tqdm(
            pool.imap(process_single_workflow, process_args),
            total=len(process_args),
            desc="Processing workflows"
        ))
    
    # aggregate
    for wf_folder, success_count, total_count in results:
        total_error_files += success_count
        total_processed += total_count
        if success_count > 0:
            logger.success(f'{wf_folder} processed {success_count}/{total_count} error files')
    
    logger.success(f'Total error files processed: {total_error_files}')
    logger.success(f'Total files processed: {total_processed}')

if __name__ == '__main__':
    # runs_dir = 'results/workflow_runs'
    # logs_dir = 'results/unzipped_workflow_detail_logs'
    # output_file = 'results/failed_runs.json'
    # get_failed_runs(runs_dir, logs_dir, output_file)
    base_dir = 'results_multiple'
    INPUT_FILE = f'{base_dir}/failed_runs.json'
    RESULTS_DIR = f'{base_dir}/unzipped_workflow_logs'
    OUTPUT_DIR = f'{base_dir}/failed_job_logs_fixed_params'
    
    logger.add(f"logs/split_failure_reason_fixed_params_{base_dir}.log")
    split_failure_reason_parallel(INPUT_FILE, RESULTS_DIR, OUTPUT_DIR)
