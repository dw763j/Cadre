# Run: find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + && python -m methods.dofix.dofix
from methods.dofix.build_channel_analyzer import BuildChannelAnalyzer
from methods.dofix.prompt_template import get_key_files_prompt, get_fix_prompt
from utils.utils import load_llm_json_response, load_llm_dockerfile_response
from loguru import logger
from utils.openai import call_model
import traceback
import os
from pathlib import Path
from config import LLM_API_BASE, LLM_API_KEY

# Matches standard_fix_process --mode; CDG ablations: cdg_no_linked / cdg_no_files; build_channel is the CDG from the paper
_MODES_WITH_BUILD_CHANNEL = frozenset({
    'standard', 'remove_key_files', 'cdg_no_linked', 'cdg_no_files',
})
_MODES_WITH_KEY_FILES = frozenset({
    'standard', 'remove_build_channel', 'cdg_no_linked', 'cdg_no_files',
})

_GO_PATH_ENTRIES = (Path.home() / "go" / "bin", Path("/usr/local/go/bin"))


def _ensure_path_entries(entries):
    seen = set()
    path_entries = []
    for raw_entry in [*os.environ.get('PATH', '').split(os.pathsep), *map(str, entries)]:
        if not raw_entry:
            continue
        entry = os.path.normpath(os.path.expanduser(raw_entry))
        if entry in seen:
            continue
        seen.add(entry)
        path_entries.append(entry)
    os.environ['PATH'] = os.pathsep.join(path_entries)


def fix_dockerfile_dofix(repo_name, workspace_path, dockerfile_path, fail_job_logs, diffs, full_build_id, model, mode='standard', dockerfile_content=None, api_address=LLM_API_BASE, api_token=LLM_API_KEY):
    try:
        results = {
            'repaired_dockerfile': None,
            'usage': {},
            'key_files_response': {},
            'code_changes': {},
            'key_files_prompt': 'EMPTY',
            'fix_prompt': 'EMPTY',
            'mode': mode
        }
        dockerfile_build_channel = None
        stages = None
        original_dockerfile = dockerfile_content

        # This section adds build_channel
        if mode in _MODES_WITH_BUILD_CHANNEL:
            _ensure_path_entries(_GO_PATH_ENTRIES)
            # logger.info(f"PATH: {os.environ['PATH']}")

            analyzer = BuildChannelAnalyzer(dockerfile_path, workspace_path, full_build_id=full_build_id)
            result = analyzer.analyze()
            logger.info(f"Analyzed Dockerfile: {dockerfile_path}")
            original_dockerfile = analyzer.dfp.content
            stages = list(analyzer.stages.keys())
            all_commands = result['all_commands']
            linked_commands = result['linked_commands']

            show_files = mode != 'cdg_no_files'
            show_linked = mode != 'cdg_no_linked'
            dockerfile_build_channel = (
                "dockerfile command | @stage | "
                + ("affected files | " if show_files else "(affected files omitted) | ")
                + ("linked commands\n" if show_linked else "(linked commands omitted)\n")
            )
            for command in sorted(all_commands.values(), key=lambda c: c['index']):
                st = command['stage']
                stage_s = ', '.join(st) if isinstance(st, list) else st
                dockerfile_build_channel += f"{command['type']}: {command['value']} | @{stage_s} | "
                if show_files:
                    if len(command['files']) > 20:
                        all_dirs_and_one_file = set()
                        for file in command['files']:
                            if Path(file).parent.as_posix() not in all_dirs_and_one_file:
                                all_dirs_and_one_file.add(file)
                                all_dirs_and_one_file.add(Path(file).parent.as_posix())
                        affected_files = list(all_dirs_and_one_file)
                    else:
                        affected_files = command['files']
                    dockerfile_build_channel += (
                        f"affected files: {', '.join(affected_files) if isinstance(affected_files, list) else affected_files} | "
                    )
                if show_linked:
                    for link in linked_commands:
                        if command['index'] in link['link_group']:
                            dockerfile_build_channel += f"linked with: {link['link_details']}, "
                dockerfile_build_channel += '\n'

        code_changes = {
            "new_files": [],
            "modified_files": [],
            "deleted_files": [],
            "key_files": {}
        }
        if diffs is not None:
            for k, v in diffs.items():
                if v['file_status'] == 'added':
                    code_changes['new_files'].append(f'./{k}')
                elif v['file_status'] == 'modified':
                    code_changes['modified_files'].append(f'./{k}')
                elif v['file_status'] == 'deleted':
                    code_changes['deleted_files'].append(f'./{k}')

        # This section adds key_files
        if mode in _MODES_WITH_KEY_FILES:
            key_files_prompt = get_key_files_prompt(stages, dockerfile_build_channel, fail_job_logs, code_changes, repo_name, mode)
            results['key_files_prompt'] = key_files_prompt
            key_files_response = None
            for _ in range(3):
                logger.debug(f"Calling key files prompt for {full_build_id}#{model}")
                key_files_response_ori = call_model(token=api_token, api_address=api_address, message=key_files_prompt, model=model, temperature=0.0, base_delay=10.0)  # includes usage stats
                try:
                    if key_files_response_ori['exceed']:
                        logger.warning(f"Error: exceed max tokens for {full_build_id}#{model}")
                        return results
                    if key_files_response_ori.get('error_type'):
                        logger.warning(
                            f"Error: model call failed ({key_files_response_ori.get('error_type')}) for {full_build_id}#{model}: {key_files_response_ori.get('error_message')}"
                        )
                        return results
                    key_files_response = load_llm_json_response(key_files_response_ori['content'])
                    if key_files_response is not None:
                        break
                except Exception as e:
                    logger.error(f"Error {e}: cannot load key files from llm response for {full_build_id}#{model}")
                    continue
            if key_files_response is None:
                logger.error(f"Error: cannot load key files from llm response for 3 times at fixing {full_build_id}#{model}")
                return results
            results['key_files_response'] = key_files_response
            results['usage']['key_files'] = key_files_response_ori['usage']
            # logger.info(f"key files of {full_build_id}#{model} with usage: {usage['key_files']}\n{key_files_response}")
        
            code_changes = {
                "new_files": [],
                "modified_files": [],
                "deleted_files": [],
                "key_files": {}
            }
            for key, value in key_files_response.items():
                if key == 'key_files': # key_files: list[dict]
                    for key_file in value:
                        code_changes['key_files'][key_file['path']] = ""
                        try:
                            with open(Path(workspace_path).joinpath(key_file['path']), encoding='utf-8') as f:
                                for line in f:
                                    if any(keyword in line for keyword in key_file['grep_keywords']):
                                        code_changes['key_files'][key_file['path']] += line
                        except Exception as e:
                            logger.warning(f"Error {e}: cannot load file {key_file['path']} from llm response")
                            continue
                else:
                    for diff_file in value:
                        if diff_file.startswith('./') and diff_file[2:] in diffs:
                            code_changes[key].append(diffs[diff_file[2:]]) # type: ignore
                        else:
                            code_changes[key].append(value) # type: ignore
            results['code_changes'] = code_changes

        if mode =='remove_key_files':
            code_changes = diffs
        fix_prompt = get_fix_prompt(original_dockerfile, dockerfile_build_channel, code_changes, fail_job_logs, repo_name, mode)

        fix_response = None
        for _ in range(3):
            fix_response_ori = call_model(token=api_token, api_address=api_address, message=fix_prompt, model=model, temperature=0.0, base_delay=10.0)
            if fix_response_ori['exceed']:
                logger.warning(f"Error: exceed max tokens for {full_build_id}#{model}")
                return results
            if fix_response_ori.get('error_type'):
                logger.warning(
                    f"Error: model call failed ({fix_response_ori.get('error_type')}) for {full_build_id}#{model}: {fix_response_ori.get('error_message')}"
                )
                return results
            fix_response = load_llm_dockerfile_response(fix_response_ori['content'])
            if fix_response is not None:
                break
        if fix_response is None:
            logger.error(f"Error: cannot load fix response from llm response for 3 times at fixing {full_build_id}")
            return results

        results['repaired_dockerfile'] = fix_response
        results['usage']['fix'] = fix_response_ori['usage']
        results['fix_prompt'] = fix_prompt
        return results

    except Exception as e:
        logger.error(f"Error: {e} at fixing {full_build_id}, {traceback.format_exc()}")
        return results