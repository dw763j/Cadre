import os
import json
import signal
import subprocess
from loguru import logger

from config import BUILD_DOCKERFILE_RUN_LOGS_DIR


def write_json(filepath, data):
    """Write run log to a JSON file."""
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        return filepath
    except Exception as e:
        logger.error(f"Error writing JSON to {filepath}: {e}")
        return None


def load_already_builds(filepath):
    """Load already built tags from a build summary file."""
    already_built_repos = []
    try:
        with open(filepath) as f:
            data = json.load(f)
            for repo in data:
                tags = []
                if not repo["builds"]:
                    already_built_repos.append(repo["repo_info"]["repo_url"])
                    continue
                build_tags = repo["repo_info"]["build_tags"]
                for build in repo["builds"]:
                    tags.append(build["ori_tag"])
                if tags:
                    for tag in tags:
                        if tag in build_tags:
                            build_tags.remove(tag)
                if not build_tags:
                    already_built_repos.append(repo["repo_info"]["repo_url"])
        return set(already_built_repos)
    except FileNotFoundError:
        logger.warning(f"File not found: {filepath}. Starting with an empty build list.")
        return {}


def load_already_runs(dir):
    """Load already run tags from a run summary directory."""
    already_run_repos = []
    try:
        for filename in os.listdir(dir):
            filepath = os.path.join(dir, filename)
            if os.path.isfile(filepath) and filename.endswith('.json'):
                with open(filepath) as f:
                    data = json.load(f)
                    tags = []
                    if not data["builds"]:
                        already_run_repos.append(data["repo_info"]["repo_url"])
                        continue
                    build_tags = data["repo_info"]["build_tags"]
                    for build in data["builds"]:
                        build_tag = build.get("ori_tag", "")
                        if build_tag != "":
                            tags.append(build["ori_tag"])
                    if tags:
                        for tag in tags:
                            if tag in build_tags:
                                build_tags.remove(tag)
                    if not build_tags:
                        already_run_repos.append(data["repo_info"]["repo_url"])
        return set(already_run_repos)
    except Exception as e:
        logger.error(f"Error loading run summaries from {dir}: {e}")
        return set()


def kill_process_tree(pid):
    """Terminate a process and all its child processes."""
    try:
        # Get child process list on Linux
        output = subprocess.check_output(f"pgrep -P {pid}", shell=True).decode()
        child_pids = [int(p) for p in output.strip().split()]
        
        # Recursively terminate all child processes
        for child_pid in child_pids:
            kill_process_tree(child_pid)
            
        # Terminate the main process
        logger.debug(f"Killing process {pid}")
        os.kill(pid, signal.SIGKILL)
    except (subprocess.CalledProcessError, ValueError):
        # No child processes or other error
        try:
            # Still attempt to kill the main process
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            # Process already gone
            pass
    except Exception as e:
        logger.debug(f"Error killing process tree: {e}")

if __name__ == "__main__":
    run_repos = load_already_runs(str(BUILD_DOCKERFILE_RUN_LOGS_DIR))
    for repo in run_repos:
        print(repo)