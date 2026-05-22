_STANDARD_LIKE_MODES = frozenset({'standard', 'cdg_no_linked', 'cdg_no_files'})


def get_key_files_prompt(stages, build_channel, dockerfile_build_failure_logs, code_changes, repo_name, mode='standard'):
    # code_changes: {"new_files": [], "modified_files": [], "deleted_files": []}
    if mode in _STANDARD_LIKE_MODES:
        prompt = f"""
## ORIGINAL Dockerfile BUILD CHAINNERLS (AFFECTED FILES IN THE BUILD PROCESS) ##
```channels
Dockerfile build stages: {stages}
Dockerfile build channels: 
{build_channel}
```
## END OF ORIGINAL BUILD CHANNELS ##

## DOCKERFILE BUILD FAILURE LOGS ##
```log
{dockerfile_build_failure_logs}
```
## END OF DOCKERFILE BUILD FAILURE LOGS ##

## CODE CHANGES ##
{code_changes}
## END OF CODE CHANGES ##

## SYSTEM ROLE ##
You are a professional programmer assistant that is good at editing Dockerfile according to the software changes.
## END OF SYSTEM ROLE ##

## INSTRUCTIONS ##
The above contains Dockerfile build stages and its build channel of project {repo_name}.
Based on the build failure logs please select the KEY files from the modified file list, the build channel list, and potential files that need to be focus on for fixing the failed Dockerfile build caused by the changed software.
For each key file, you can use key words to grep needed infomation within the file. 
For example, "key_files":[{{"path": "xx", "grep_keywords": ["xx", "xx"]}},...].
If the error is not relavent to the mentioned files, retrun empty entries.
## END OF INSTRUCTIONS ##

## RETURN RULES ##
RETURN THE KEY FILES YOU SELECTED WITH PATH RELATED TO THE PROJECT ROOT(EG. "./xx/xx") AS: 
```json {{"new_files": [...], "modified_files": [...], "deleted_files": [...], "key_files": [...]}} ```
RETURN ONLY THE JSON BLOCK, DO NOT MAKE ANY OTHER EXPLANATIONS.
## END OF RETURN RULES ##
"""
    elif mode == 'remove_build_channel':
        prompt = f"""
## DOCKERFILE BUILD FAILURE LOGS ##
```log
{dockerfile_build_failure_logs}
```
## END OF DOCKERFILE BUILD FAILURE LOGS ##

## CODE CHANGES ##
{code_changes}
## END OF CODE CHANGES ##

## SYSTEM ROLE ##
You are a professional programmer assistant that is good at editing Dockerfile according to the software changes.
## END OF SYSTEM ROLE ##

## INSTRUCTIONS ##
Based on the build failure logs of {repo_name}, please select the KEY files from the modified file list, and potential files that need to be focus on for fixing the failed Dockerfile build caused by the changed software.
For each key file, you can use key words to grep needed infomation within the file. 
For example, "key_files":[{{"path": "xx", "grep_keywords": ["xx", "xx"]}},...].
If the error is not relavent to the mentioned files, retrun empty entries.
## END OF INSTRUCTIONS ##

## RETURN RULES ##
RETURN THE KEY FILES YOU SELECTED WITH PATH RELATED TO THE PROJECT ROOT(EG. "./xx/xx") AS: 
```json {{"new_files": [...], "modified_files": [...], "deleted_files": [...], "key_files": [...]}} ```
RETURN ONLY THE JSON BLOCK, DO NOT MAKE ANY OTHER EXPLANATIONS.
## END OF RETURN RULES ##
"""
    else:
        raise ValueError(f"Invalid mode: {mode}")
    return prompt

def get_fix_prompt(original_dockerfile, build_channel, code_changes, dockerfile_build_failure_logs, repo_name, mode='standard'):
    if mode in _STANDARD_LIKE_MODES:
        prompt = f"""
## ORIGINAL DOCKERFILE ##
```Dockerfile
{original_dockerfile}
```
## END OF ORIGINAL DOCKERFILE ##

## DOCKERFILE BUILD COMMAND ##
```channels
{build_channel}
```
## END OF DOCKERFILE BUILD COMMAND ##

## DETAIL OF CODE CHANGES ##
```code_changes
{code_changes}
```
## END OF CODE CHANGES ##

## DOCKERFILE BUILD FAILURE LOGS ##
```log
{dockerfile_build_failure_logs}
```
## END OF DOCKERFILE BUILD FAILURE LOGS ##

## SYSTEM ROLE ##
You are a professional programmer that is good at FIXING Dockerfile according to the software changes.
## END OF SYSTEM ROLE ##

## INSTRUCTIONS ##
The above contains original dockerfile and its original build channel of project {repo_name}.
According to the provided code changes, build channels and the build failure log it cased, fix the Dockerfile to make it build successfully. 
## END OF INSTRUCTIONS ##

## RETURN RULES ##
RETURN ONLY THE FIXED DOCKERFILE WITH ```Dockerfile ...CONTENT... ``` SURRONDING.
DO NOT MAKE ANY OTHER EXPLANATIONS, DO NOT USE ANY OTHER MARKS.
## END OF RETURN RULES ##
"""
    elif mode == 'remove_build_channel':
        prompt = f"""
## ORIGINAL DOCKERFILE ##
```Dockerfile
{original_dockerfile}
```
## END OF ORIGINAL DOCKERFILE ##

## DETAIL OF CODE CHANGES ##
```code_changes
{code_changes}
```
## END OF CODE CHANGES ##

## DOCKERFILE BUILD FAILURE LOGS ##
```log
{dockerfile_build_failure_logs}
```
## END OF DOCKERFILE BUILD FAILURE LOGS ##

## SYSTEM ROLE ##
You are a professional programmer that is good at FIXING Dockerfile according to the software changes.
## END OF SYSTEM ROLE ##

## INSTRUCTIONS ##
The above contains original dockerfile and the build failure log of {repo_name}.
According to the provided code changes and the build failure log it cased, fix the Dockerfile to make it build successfully. 
## END OF INSTRUCTIONS ##

## RETURN RULES ##
RETURN ONLY THE FIXED DOCKERFILE WITH ```Dockerfile ...CONTENT... ``` SURRONDING.
DO NOT MAKE ANY OTHER EXPLANATIONS, DO NOT USE ANY OTHER MARKS.
## END OF RETURN RULES ##
"""
    elif mode == 'remove_key_files':
        # Same template as standard; build_channel in dofix may be full or ablated format
        prompt = f"""
## ORIGINAL DOCKERFILE ##
```Dockerfile
{original_dockerfile}
```
## END OF ORIGINAL DOCKERFILE ##

## DOCKERFILE BUILD COMMAND ##
```channels
{build_channel}
```
## END OF DOCKERFILE BUILD COMMAND ##

## DETAIL OF CODE CHANGES ##
```code_changes
{code_changes}
```
## END OF CODE CHANGES ##

## DOCKERFILE BUILD FAILURE LOGS ##
```log
{dockerfile_build_failure_logs}
```
## END OF DOCKERFILE BUILD FAILURE LOGS ##

## SYSTEM ROLE ##
You are a professional programmer that is good at FIXING Dockerfile according to the software changes.
## END OF SYSTEM ROLE ##

## INSTRUCTIONS ##
The above contains original dockerfile and its original build channel of project {repo_name}.
According to the provided code changes, build channels and the build failure log it cased, fix the Dockerfile to make it build successfully. 
## END OF INSTRUCTIONS ##

## RETURN RULES ##
RETURN ONLY THE FIXED DOCKERFILE WITH ```Dockerfile ...CONTENT... ``` SURRONDING.
DO NOT MAKE ANY OTHER EXPLANATIONS, DO NOT USE ANY OTHER MARKS.
## END OF RETURN RULES ##
```
"""
    else:
        raise ValueError(f"Invalid mode: {mode}")
    return prompt