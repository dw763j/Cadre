# Run: find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + && python -m build_dockerfile.build_fixed_docker
# Default layout by method/mode/model_slug (matches methods.dofix.standard_fix_process):
#   results/fixed_dockerfiles/<method>/<mode>/<model_slug>
#   results/fixed_docker_builds/<method>/<mode>/<model_slug>
# On disk, Dockerfile files are named: {full_build_id}#{method}#{model_slug} (/ replaced with _)
# Override paths or use multiple methods explicitly:
#   python -m build_dockerfile.build_fixed_docker --method dofix --mode standard --model DeepSeek-V3
#   python -m build_dockerfile.build_fixed_docker --fix_methods dofix#DeepSeek-V3 pure-llm#DeepSeek-V3 \
#       --fixed_dockerfile_base_path results/custom_fixed --output_dir results/custom_builds
import argparse
import os
import json
import time
import signal
import threading
import subprocess
from importlib import import_module
from pathlib import Path
from datetime import datetime
from multiprocessing import Process, Manager
from typing import Any
from loguru import logger
from tqdm import tqdm

from config import (
    MAX_BUILDERS,
    REGISTRY_PORT,
    DEFAULT_DATASET_PATH,
    CLONED_REPOS_DIR,
    LLM_MODEL,
    results_fixed_docker_builds_dir,
    results_fixed_dockerfiles_dir,
    slug_model,
)
from build_dockerfile.utils import write_json, kill_process_tree
from build_dockerfile.docker_utils import discover_multi_platform_builders, prune_builder_cache
from utils.GithubRepo import GithubRepo, get_clean_repo
from utils.check_repos import check_repos_status

# Global stop flag
stop_requested = False


def default_fix_method_tag(method: str, model: str) -> str:
    """Align with standard_fix_process per-entry tag: method#model_slug."""
    return f"{method}#{slug_model(model)}"


def base_fix_method(fix_method_tag: str) -> str:
    """fix_method_tag like dofix#DeepSeek-V3 -> dofix."""
    return fix_method_tag.split("#", 1)[0]

def signal_handler(sig, frame):
    """Handle stop signals."""
    global stop_requested
    if not stop_requested:
        logger.info("Stop signal received; will stop after the current build completes. Press Ctrl+C again to force exit.")
        stop_requested = True
    else:
        logger.warning("Second stop signal received; forcing exit.")
        exit(1)

def get_image_info(registry_url: str, builder: str):
    try:
        # Run command and capture output
        cmd = ["docker", "buildx", "imagetools", "inspect", registry_url, "--raw", "--builder", builder]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Parse JSON output
        image_info = json.loads(result.stdout)
        return image_info
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed: {e}")
        return None

def delete_image(image_tag: str):
    if f'localhost:{REGISTRY_PORT}/' in image_tag:
        image_tag = image_tag.replace(f'localhost:{REGISTRY_PORT}/', '')
    cmd = f"docker run --rm anoxis/registry-cli -r http://localhost:{REGISTRY_PORT} -i {image_tag} --delete-all"
    try:
        subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        logger.error(f"Error deleting image {image_tag}: {e}")
        return False
    return True

def update_progress_info(progress_info: dict[str, Any] | None, progress_lock: Any | None):
    if progress_info is not None and progress_lock is not None:
        try:
            with progress_lock:
                progress_info['builds_done'] = progress_info.get('builds_done', 0) + 1
        except Exception as e:
            logger.error(f"Error updating progress info: {e}")

class FixedDockerBuilder:
    """Builder for repaired Dockerfiles; reuses docker_builder.py logic."""
    
    def __init__(self, repo_url: str, repo_path: str, fixed_dockerfile_base_path: str | None = None, builder: str = "multi-platform-0",
                 prune_every_n_builds: int = 4, build_log_dir: str | None = None, run_mode: str = 'build_dockerfile'):
        """
        Initialize the repaired-Dockerfile builder
        
        Args:
            repo_url: Repository URL
            repo_path: Repository root directory
            build_log_dir: Build log directory
            run_log_path: Run log path
            locks: Lock objects
            fixed_dockerfile_base_path: Base path for repaired Dockerfiles
        """
        self.repo_url = repo_url
        self.builder = builder
        self.fixed_dockerfile_base_path = fixed_dockerfile_base_path or ""
        self.build_log_dir = build_log_dir or ""
        self.run_mode = run_mode

        self.prune_every_n_builds = prune_every_n_builds
        self.build_count_since_prune = 0    
        self.timeout_times = 0
        try:
            self.github_repo = GithubRepo(repo_url)
            self.repo_path = Path(repo_path)
            self.git_repo = get_clean_repo(self.repo_path)
            if self.git_repo is None:
                raise Exception(f"Failed to get clean repo for {repo_url}")
            self.original_branch = self.git_repo.active_branch
            logger.info(f"FixedDockerBuilder for {repo_url} initialized")

        except Exception as e:
            logger.error(f"Error initializing FixedDockerBuilder for {repo_url}: {e}")
            self.github_repo = None
            self.repo_path = None
            self.git_repo = None
            self.original_branch = None

    def should_stop(self):
        """Check whether a stop signal was received."""
        global stop_requested
        return stop_requested
    
    def _execute_docker_build(self, dockerfile_path: str, commit_id: str, build_params: dict[str, Any], full_build_tag: str) -> dict[str, Any]:
        """
        Run a Docker build
        
        Args:
            dockerfile_path: Path to the Dockerfile
            commit_id: Commit SHA
            build_params: Build parameters
            full_build_tag: Full build tag
            
        Returns:
            Build result dictionary
        """
        build_log_file = Path(self.build_log_dir) / f"build#{full_build_tag}.log"
        full_tag = f"localhost:{REGISTRY_PORT}/{full_build_tag}".lower().replace('#', '_')

        logger.info(f"Building Docker image from {dockerfile_path} for {full_tag} ...")

        build_logs = []
        error_msg = ''
        process = None
        start_time = time.time()
        timestamp = int(start_time)
        build_flag = False
        try:
            self.git_repo = get_clean_repo(self.git_repo)
            self.git_repo.git.checkout(commit_id) # type: ignore
            logger.debug(f"Checked out {commit_id} from {self.original_branch} of {self.repo_url}")
            
            # Resolve build context path
            context_path = build_params.get('context', '.')
            if context_path.startswith('http') or context_path.startswith('git@') or '***' in context_path:
                context_path = '.'

            if not os.path.isabs(context_path):
                # Relative path, relative to repo root
                context_path = os.path.join(str(self.repo_path), context_path)

            cmd = [
                "docker", "buildx", "build", "-t", full_tag, "--push", # note: push to local registry
                "-f", dockerfile_path,
                "--progress=plain",
                "--builder", self.builder,
                str(context_path)
            ]
            
            # Optional platform argument
            if 'platform' in build_params:
                platforms = build_params['platform']
                if isinstance(platforms, list):
                    if len(platforms) == 1:
                        # Single-platform build
                        cmd.extend(["--platform", str(platforms[0])])
                    else:
                        # Multi-platform build
                        platform_list = ','.join(str(p) for p in platforms)
                        cmd.extend(["--platform", platform_list])
                else:
                    # Single platform string
                    cmd.extend(["--platform", str(platforms)])
            
            # Optional target argument
            if 'target' in build_params:
                cmd.extend(["--target", build_params['target']])

            if 'build-arg' in build_params:
                for key, value in build_params['build-arg'].items():
                    cmd.extend(["--build-arg", f"{key}={value}"])
            
            # Log the full build command
            logger.debug(f"Building {full_tag} with cmd: {cmd}")

            # Use threads to collect stdout and stderr concurrently
            def read_output(pipe, is_error=False):
                for line in iter(pipe.readline, ''):
                    line = line.strip()
                    if line:
                        log_line = f"ERROR: {line}" if is_error else line
                        build_logs.append(log_line)

            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, bufsize=1)

            # Create threads to read stdout and stderr
            stdout_thread = threading.Thread(target=read_output, args=(process.stdout,))
            stderr_thread = threading.Thread(target=read_output, args=(process.stderr,))
            
            # Set as daemon threads and start
            stdout_thread.daemon = True
            stderr_thread.daemon = True
            stdout_thread.start()
            stderr_thread.start()
            
            try:
                # wait for the process to finish; reuse existing 30-minute timeout
                returncode = process.wait(timeout=1800)  # Timeout of 30 mins
            except subprocess.TimeoutExpired:
                self.timeout_times += 1
                logger.error(f"Build timeout after 30 minutes for {full_tag}, timeout {self.timeout_times} times for {self.repo_url}")
                # Force-terminate the process tree
                kill_process_tree(process.pid)
                returncode = -2  # Indicate timeout

            # Wait for log collection threads to finish
            stdout_thread.join(timeout=10)
            stderr_thread.join(timeout=10)
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                logger.warning("Log collection threads did not complete in time, some logs may be missing")
                
            if returncode == -2:
                error_msg = "Build timeout after 30 minutes"
                raise Exception(f"Docker build failed with exit code {returncode}: {error_msg}, logs at {build_log_file}")
            elif returncode != 0:
                if not error_msg:
                    # If stderr had no error message, check stdout
                    for log in reversed(build_logs):
                        if "ERROR" in log or "error" in log or "failed" in log:
                            error_msg = log
                            break
                raise Exception(f"Docker build failed with exit code {returncode}: {error_msg}, logs at {build_log_file}")

            # If we got here, build was successful
            build_time = time.time() - start_time
            image_info = get_image_info(full_tag, self.builder)

            if image_info is None:
                logger.error(f"Failed to get image info for {full_tag}")
                raise Exception(f"Failed to get image info for {full_tag}, logs at {build_log_file}")
            image_size = sum(manifest['size'] for manifest in image_info['manifests']) / (1024 ** 2)  # Size in MB
    
            with open(build_log_file, 'w') as f:
                f.write('\n'.join(build_logs))

            logger.success(
                f"Image built for {full_tag} by {self.builder}, "
                f"Build Time: {build_time:.2f} seconds, Size: {image_size:.2f} MB, "
                f"Build logs saved to {build_log_file}"
            )
            
            success_result = {
                "commit_id": commit_id,
                "status": "success",
                "build_time": build_time,
                "image_size_mb": image_size,
                "image_inspect": image_info,
                "build_log_file": str(build_log_file),
                "timestamp": timestamp,
                "context_path": context_path,
                "full_build_tag": full_tag
            }
            
            # Clean up image and restore git repo state
            delete_image(full_tag)
            logger.info(f"Deleted built image {full_tag}")
            self.git_repo = get_clean_repo(self.git_repo)
            build_flag = True
            return success_result

        except Exception as e:
            exception_type = type(e).__name__
            logger.error(f"Exception {exception_type} at {full_tag}: {str(e)}")
            build_time = time.time() - start_time
            self.git_repo = get_clean_repo(self.git_repo)
            if process and process.poll() is None:
                logger.warning("Process is still running, killing it...")
                kill_process_tree(process.pid)

            # Save all collected logs; reuse existing log-save logic
            with open(build_log_file, 'w') as f:
                f.write('\n'.join(build_logs))
                f.write(f"\n\nFIXED-DOCKERBUILDER-BUILD-FAILED: {str(e)}")

            failed_result = {
                "commit_id": commit_id,
                "status": "failed" if returncode != -2 else "timeout",
                "error": error_msg + '\n' + str(e) if error_msg else str(e),
                "build_time": build_time,
                "build_log_file": str(build_log_file),
                "timestamp": timestamp,
                "context_path": context_path,
                "full_build_tag": full_tag
            }

            return failed_result
        finally:
            # per-builder prune based on count threshold
            try:
                if build_flag:
                    self.build_count_since_prune += 1 # count only successful builds to retain more cache
                if self.prune_every_n_builds > 0 and self.build_count_since_prune !=0 and (self.build_count_since_prune % self.prune_every_n_builds == 0):
                    logger.debug(f"Pruning builder cache for {self.builder} after {self.build_count_since_prune} builds...")
                    self.build_count_since_prune = 0
                    prune_builder_cache(self.builder)
            except Exception as e:
                logger.warning(f"Failed to execute per-builder prune for {self.builder}: {e}")

class FixedDockerProcessor:
    """Processor for repaired Dockerfiles; manages the build workflow and concurrency."""
    
    def __init__(self, dataset_path: str, fixed_dockerfile_base_path: str, fix_methods: list[str],
                 repo_root: str, output_dir: str, max_workers: int = 8,
                 builder_pool: list[str] | None = None, prune_every_n_builds: int = 4,
                 run_mode: str = 'build_dockerfile', repair_model: str | None = None):
        """
        Initialize the processor
        
        Args:
            diff_dataset_dir: Diff dataset directory path
            fixed_dockerfile_base_path: Base path for repaired Dockerfiles
            output_dir: Output directory
            max_workers: Maximum concurrent worker processes
        """
        logger.info("Starting fixed Dockerfile building process...")
        if not os.path.exists(dataset_path):
            logger.error(f"Dataset directory does not exist: {dataset_path}")
            return
        
        # Load dataset
        with open(dataset_path, encoding='utf-8') as f:
            self.dataset = json.load(f)
        
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
        logger.info(f"Loaded dataset for {len(self.dataset)} repositories, total full build ids in the dataset: {total_full_build_ids}")

        self.fixed_dockerfile_base_path = Path(fixed_dockerfile_base_path)
        self.repo_root = Path(repo_root)
        self.output_dir = Path(output_dir)
        self.max_workers = max_workers
        self.builder_pool = builder_pool or ["multi-platform-0"]
        self.prune_every_n_builds = prune_every_n_builds
        self.run_mode = run_mode
        self.fix_methods = fix_methods
        self.repair_model = repair_model or LLM_MODEL
        # Create output directories
        self.build_logs_dir = self.output_dir / "build_logs"
        self.run_logs_dir = self.output_dir / "run_logs"
        self.results_dir = self.output_dir / "results"
        
        for dir_path in [self.build_logs_dir, self.run_logs_dir, self.results_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
    
    def process_repo(self, repo_key, build_infos, builder_name, progress_info=None, progress_lock=None):
        """
        Process all repaired Dockerfile builds for one repo; reuses existing repo processing logic
        
        Args:
            repo_key: Repository identifier
            build_infos: All build entries for the repo
            locks_dict: Lock dictionary
            
        Returns:
            Processing result
        """
        owner, repo_name = repo_key.split('#')
        repo_url = f"https://github.com/{owner}/{repo_name}"
        
        # Create/locate per-repo run log file
        repo_run_log_path = self.run_logs_dir / f"{repo_key}.json"

        # Load existing results for per-full_build_id resume
        results: dict[str, Any] | None = None
        processed_full_build_tags = set()
        if os.path.exists(repo_run_log_path):
            try:
                with open(repo_run_log_path, encoding='utf-8') as f:
                    existing = json.load(f)
                # Validate structure and reuse existing stats
                if isinstance(existing, dict) and "builds" in existing and isinstance(existing.get("builds"), list):
                    results = existing
                    for build in existing.get("builds", []):
                        if not isinstance(build, dict):
                            continue
                        # Extract full_build_id from full_build_tag (run_folder#build_id#fix_method -> first two segments)
                        tag = build.get("full_build_tag")
                        if build.get("status") == "error":
                            error_log = build.get("error_log")
                            if "failed to solve: Canceled: context canceled" in error_log:
                                logger.warning(f"Canceled: context canceled for {tag}, keep it to rebuild.")
                                build["status"] = "rebuilding"
                                build["error"] = None
                                build["timestamp"] = None
                                continue
                        if build.get("status") == "skipped" and "Fixed Dockerfile not found" in build.get("error"):
                            logger.warning(f"Fixed Dockerfile not found for {tag}, keep it to rebuild.")
                            build["status"] = "rebuilding"
                            build["error"] = None
                            build["timestamp"] = None
                            continue
                        processed_full_build_tags.add(tag)
                else:
                    logger.warning(f"Invalid existing run log format for {repo_key}, restarting results file")
            except Exception as e:
                logger.warning(f"Failed to load existing run log for {repo_key}: {e}")

        if results is None:
            results = {
                "repo_info": {
                    "repo_key": repo_key,
                    "repo_url": repo_url,
                    "total_build_infos": len(build_infos)
                },
                "builds": [],
                "summary": {
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "skipped": 0,
                    "error": 0
                }
            }
        if repo_key == "gpustack#gpustack":  # skip gpustack builds
            return results

        builder: FixedDockerBuilder | None = None
        try:
            # Initialize DockerBuilder
            builder = FixedDockerBuilder(
                repo_url=repo_url,
                repo_path=str(self.repo_root / repo_key),
                build_log_dir=str(self.build_logs_dir),
                fixed_dockerfile_base_path=str(self.fixed_dockerfile_base_path),
                builder=builder_name,
                prune_every_n_builds=self.prune_every_n_builds,
                run_mode=self.run_mode
            )
            
            if not builder.git_repo:
                logger.error(f"Failed to initialize repository {repo_url}")
                results["error"] = "Failed to initialize repository"
                write_json(repo_run_log_path, results)
                return results
            
            logger.info(f"Processing repository {repo_key} with {len(build_infos)} build infos")

            # Process each commit (supports per-full_build_id resume)
            for full_build_id, build_info in build_infos.items():
                
                if stop_requested:
                    logger.warning(f"Stop signal detected, stopping processing for repo {repo_key}")
                    break
                # if builder.timeout_times >= 3:
                #     logger.error(f"Timeout {builder.timeout_times} times for {builder.repo_url}, force stop.")
                #     raise Exception(f"Timeout {builder.timeout_times} times for {builder.repo_url}, force stop.")

                commit_id = build_info.get('head_sha')
                build_params = build_info.get('build_params', {})
                
                if not build_params:
                    logger.warning(f"No docker build contexts found for commit {commit_id}")
                    # Update global build progress
                    update_progress_info(progress_info, progress_lock)
                    continue

                for fix_method in self.fix_methods:
                    # Same as standard_fix_process: {full_build_id}#{method}#{model}, then replace /
                    stem = f"{full_build_id}#{fix_method}".replace("/", "_")
                    fixed_dockerfile_path: str | None = None

                    if self.run_mode == "build_dockerfile":
                        fixed_dockerfile_path = str(self.fixed_dockerfile_base_path / stem)
                    elif self.run_mode == "repair_dockerfile" and base_fix_method(fix_method) == "dofix":
                        # FIXME on-the-fly repair should align with standard fix flow; hardcoded model name only replaced here
                        dofix_module = import_module("methods.dofix.dofix")
                        fix_fn = getattr(dofix_module, "fix_dockerfile", None)
                        if not callable(fix_fn):
                            raise AttributeError("methods.dofix.dofix.fix_dockerfile is not callable")
                        fix_fn(
                            repo_key,
                            builder.git_repo,
                            commit_id,
                            build_params,
                            build_info.get("error_log"),
                            build_info.get("diff_info"),
                            self.repair_model,
                        )
                        fixed_dockerfile_path = str(self.fixed_dockerfile_base_path / stem)
                    elif base_fix_method(fix_method) == "shipwright":
                        logger.warning("shipwright branch not implemented yet; skipping")
                        update_progress_info(progress_info, progress_lock)
                        continue
                    else:
                        logger.error(f"Invalid fix method / run_mode combination: {fix_method} ({self.run_mode})")
                        update_progress_info(progress_info, progress_lock)
                        continue

                    if not fixed_dockerfile_path:
                        update_progress_info(progress_info, progress_lock)
                        continue
                    
                    # Resume: skip if full_build_id#fix_method already appears in results
                    if f"{full_build_id}#{fix_method}" in processed_full_build_tags:
                        logger.debug(f"Resume: skip already processed full_build_id {full_build_id}#{fix_method} for repo {repo_key}")
                        update_progress_info(progress_info, progress_lock)
                        continue

                    # Check whether the repaired Dockerfile exists
                    if not os.path.exists(fixed_dockerfile_path):
                        logger.warning(f"Fixed Dockerfile not found for {full_build_id}#{fix_method}")
                        build_result = {
                            "full_build_tag": f"{full_build_id}#{fix_method}",
                            "fix_method": fix_method,
                            "commit_id": commit_id,
                            "status": "skipped",
                            "error": "Fixed Dockerfile not found",
                        }
                    else: # repaired Dockerfile exists
                        with open(fixed_dockerfile_path) as f:
                            fixed_dockerfile = f.read()
                        skip_list = ["nvidia/tritonserver", "nvidia/cuda", "FROM pytorch", "install torch"]
                        if any(skip_keyword in fixed_dockerfile for skip_keyword in skip_list):
                            logger.warning(f"Skipping building fixed Dockerfile for {full_build_id}#{fix_method} because it contains one of {skip_list}")  # noqa: E501
                            build_result = {
                                "full_build_tag": f"{full_build_id}#{fix_method}",
                                "fix_method": fix_method,
                                "commit_id": commit_id,
                                "status": "skipped",
                                "error": f"Fixed Dockerfile contains one of {skip_list}",
                            }
                        else: # does not contain nvidia etc.
                            try:
                                # Run build
                                logger.info(f"Building fixed Dockerfile from {full_build_id}#{fix_method} for {commit_id}")
                                # FIXME builder is created per repo; consider one builder per fixmethod x repo
                                build_result = builder._execute_docker_build(
                                    fixed_dockerfile_path, commit_id, build_params, f"{full_build_id}#{fix_method}"
                                )
                                build_result.update({
                                    "full_build_tag": f"{full_build_id}#{fix_method}",
                                    "fix_method": fix_method,
                                    "commit_id": commit_id,
                                })

                            except Exception as e:
                                logger.error(f"Error building fixed Dockerfile for {full_build_id}#{fix_method}: {e}")
                                build_result = {
                                    "full_build_tag": f"{full_build_id}#{fix_method}",
                                    "fix_method": fix_method,
                                    "commit_id": commit_id,
                                    "status": "error",
                                    "error": str(e),
                                }

                    # Append to build results list
                    builds = results.get("builds")
                    if not isinstance(builds, list):
                        builds = []
                        results["builds"] = builds
                    if not isinstance(build_result, dict):
                        build_result = dict(build_result)
                    builds.append(build_result)
                    # Update summary stats
                    summary = results.get("summary")
                    if not isinstance(summary, dict):
                        summary = {}
                        results["summary"] = summary
                    summary["total"] = len([build for build in builds if isinstance(build, dict) and build.get("status") not in ["rebuilding", "skipped"]])
                    status = build_result.get("status", "unknown")

                    summary[status] = len([build for build in builds if isinstance(build, dict) and build.get("status") == status])

                    # Persist results incrementally
                    write_json(repo_run_log_path, results)

                    # Update global build progress
                    update_progress_info(progress_info, progress_lock)
            
            logger.success(
                f"Repository {repo_key} processing completed. "
                f"Total: {results.get('summary', {}).get('total', 0)}, "
                f"Success: {results.get('summary', {}).get('success', 0)}, "
                f"Failed: {results.get('summary', {}).get('failed', 0)}, "
                f"Skipped: {results.get('summary', {}).get('skipped', 0)}, "
                f"Error: {results.get('summary', {}).get('error', 0)}"
            )
            
        except Exception as e:
            logger.error(f"Error processing repository {repo_key}: {e}")
            results["error"] = str(e)
            write_json(repo_run_log_path, results)
        finally:
            if builder is not None:
                prune_builder_cache(builder.builder)  # prune builder cache after each repo; cache cannot be shared across repos
        return results

    def _process_repo_and_append_result(self, repo_key, build_infos, builder_name, shared_results, progress_info=None, progress_lock=None):
        """
        Process one repo and append its result to the shared list (multiprocessing).
        
        Args:
            repo_key: Repository identifier
            build_infos: All build entries for the repo
            builder_name: Builder name
            shared_results: Shared result list
        """
        try:
            result = self.process_repo(repo_key, build_infos, builder_name, progress_info=progress_info, progress_lock=progress_lock)
            shared_results.append(result)
        except Exception as e:
            logger.error(f"Error in processing repo {repo_key}: {e}")
            shared_results.append({
                "repo_key": repo_key,
                "status": "error",
                "error": str(e)
            })

    def process_all_repos_parallel(self):
        """
        Process all repos in parallel; reuses existing concurrency control
            
        Returns:
            List of per-repo processing results
        """
        results = []
        processes = []  # list of tuples (Process, builder_name)
        
        with Manager() as manager:
            shared_results = manager.list()
            progress_info = manager.dict()
            progress_info['builds_done'] = 0
            progress_lock = manager.Lock()
            repos_to_process = []
            for repo_key, build_infos in self.dataset.items():
                repos_to_process.append((repo_key, build_infos))

            available_builders = list(self.builder_pool)
            total_repos = len(repos_to_process)
            # Estimate total builds: each build_info x each fix_method
            planned_total_builds = sum(len(build_infos) * len(self.fix_methods) for _, build_infos in repos_to_process)
            repo_pbar = tqdm(total=total_repos, desc="Repos", position=0)
            build_pbar = tqdm(total=planned_total_builds, desc="Builds", position=1)
            last_builds_done = 0
            
            while repos_to_process or processes:
                if stop_requested:
                    logger.warning("Stop signal detected, stopping processing more repos...")
                    break

                # Reclaim finished processes and return builders
                for p, b in processes[:]:
                    if not p.is_alive():
                        p.join()
                        processes.remove((p, b))
                        available_builders.append(b)
                        repo_pbar.update(1)

                # Start new processes when a builder and pending repo are available, under concurrency cap
                while available_builders and repos_to_process and len(processes) < self.max_workers:
                    builder_name = available_builders.pop(0)
                    repo_key, build_infos = repos_to_process.pop(0)
                    p = Process(
                        target=self._process_repo_and_append_result,
                        args=(repo_key, build_infos, builder_name, shared_results, progress_info, progress_lock)
                    )
                    p.start()
                    processes.append((p, builder_name))
                    logger.info(f"Started process for repo {repo_key} on builder {builder_name}")

                if processes and (not available_builders or not repos_to_process):
                    time.sleep(1)

                # Refresh build progress bar
                try:
                    current_done = int(progress_info.get('builds_done', 0))
                    if current_done > last_builds_done:
                        build_pbar.update(current_done - last_builds_done)
                        last_builds_done = current_done
                except Exception as _:
                    pass

            # Wait for all processes to finish
            for p, _ in processes:
                p.join()

            results = list(shared_results)
            # Close progress bars
            try:
                # Final sync
                current_done = int(progress_info.get('builds_done', 0))
                if current_done > last_builds_done:
                    build_pbar.update(current_done - last_builds_done)
            except Exception as _:
                pass
            repo_pbar.close()
            build_pbar.close()
        
        # Save overall summary
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # end-of-run timestamp
        summary_file = self.results_dir / f"fixed_docker_build_summary_{timestamp}.json"
        
        summary = {
            "timestamp": timestamp,
            "total_repos": len(results),
            "repos_processed": len([r for r in results if "error" not in r]),
            "repos_with_error": len([r for r in results if "error" in r]),
            "total_builds": sum(r.get("summary", {}).get("total", 0) for r in results),
            "successful_builds": sum(r.get("summary", {}).get("success", 0) for r in results),
            "failed_builds": sum(r.get("summary", {}).get("failed", 0) for r in results),
            "skipped_builds": sum(r.get("summary", {}).get("skipped", 0) for r in results),
            "error_builds": sum(r.get("summary", {}).get("error", 0) for r in results),
            "timeout_builds": sum(r.get("summary", {}).get("timeout", 0) for r in results),
            "results": results
        }
        
        write_json(summary_file, summary)
        logger.success(f"Fixed Dockerfile build process completed. Summary saved to {summary_file}")
        logger.info(f"Total repos: {summary['total_repos']}, Total builds: {summary['total_builds']}")

        cmd = "docker exec registry bin/registry garbage-collect /etc/distribution/config.yml -m"
        subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info("Garbage collected for registry")
        return results

SUPPORTED_BASE_FIX_METHODS = ("dofix", "pure-llm", "parfum", "flakidock", "shipwright")


def _parse_args() -> argparse.Namespace:
    fm = ",".join(SUPPORTED_BASE_FIX_METHODS)
    parser = argparse.ArgumentParser(description="Batch-build/validate repaired Dockerfiles from a dataset (BuildKit).")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH), help="Dataset JSON.")
    parser.add_argument("--method", default="dofix", choices=("dofix", "parfum", "pure-llm", "flakidock", "shipwright"), help="Repair method (with --mode/--model determines default I/O dirs).")
    parser.add_argument(
        "--mode",
        default="standard",
        choices=("standard", "remove_build_channel", "remove_key_files", "cdg_no_linked", "cdg_no_files", "nodiff", "upgrade_response_deal"),
        help="Matches standard_fix_process (affects default results dir names only).",
    )
    parser.add_argument("--model", type=str, default=LLM_MODEL, help=f"Model name; default {LLM_MODEL!r}.")
    parser.add_argument("--fixed_dockerfile_base_path", default=None, help="Repaired Dockerfile root; default results/fixed_dockerfiles/<method>/<mode>/<slug>.")
    parser.add_argument("--repo_root", default=str(CLONED_REPOS_DIR), help="Cloned repos root (owner#repo subdirs).")
    parser.add_argument("--output_dir", default=None, help="Build logs and summary root; default results/fixed_docker_builds/<method>/<mode>/<slug>.")
    parser.add_argument("--fix_methods", nargs="*", default=None, metavar="TAG", help=f"Artifact tag list (e.g. dofix#X); default method#slug; base methods: {fm}.")
    parser.add_argument("--max_workers", type=int, default=4, help="Concurrent repos.")
    parser.add_argument("--builder_pool", nargs="+", default=None, help=f"buildx builder list; default from docker buildx ls multi-platform-*, else multi-platform-0..{MAX_BUILDERS - 1}.")
    parser.add_argument("--prune_every_n_builds", type=int, default=8, help="buildx prune per builder after N successful builds.")
    parser.add_argument("--run_mode", default="build_dockerfile", choices=["build_dockerfile", "repair_dockerfile"], help="build_dockerfile=use existing Dockerfiles; repair_dockerfile=on-the-fly repair (experimental).")
    return parser.parse_args()


def main():
    """Main entry point."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    args = _parse_args()

    dataset_path = args.dataset
    slug = slug_model(args.model)
    if args.fixed_dockerfile_base_path is not None:
        fixed_dockerfile_base_path = args.fixed_dockerfile_base_path
    else:
        fixed_dockerfile_base_path = str(results_fixed_dockerfiles_dir(args.method, args.mode, args.model))
    if args.output_dir is not None:
        output_dir = args.output_dir
    else:
        output_dir = str(results_fixed_docker_builds_dir(args.method, args.mode, args.model))

    repo_root = args.repo_root
    max_workers = args.max_workers
    prune_every_n_builds = args.prune_every_n_builds
    if args.fix_methods:
        fix_methods = list(args.fix_methods)
    else:
        fix_methods = [default_fix_method_tag(args.method, args.model)]

    run_mode = args.run_mode
    if args.builder_pool:
        builder_pool = list(args.builder_pool)
    else:
        builder_pool = discover_multi_platform_builders()
        if not builder_pool:
            builder_pool = [f"multi-platform-{i}" for i in range(MAX_BUILDERS)]
            logger.warning(
                "No buildx builders with prefix multi-platform- found; "
                f"falling back to multi-platform-0..multi-platform-{MAX_BUILDERS - 1}"
            )
        else:
            logger.info(f"Collected builder pool from docker buildx ls ({len(builder_pool)} builders): {builder_pool}")

    unknown_methods = [m for m in fix_methods if base_fix_method(m) not in SUPPORTED_BASE_FIX_METHODS]
    if unknown_methods:
        logger.warning(
            f"Unknown --fix_methods base method names {unknown_methods}. "
            f"Supported: {', '.join(SUPPORTED_BASE_FIX_METHODS)}"
        )

    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(output_dir, f"fixed_docker_build_{timestamp}.log")
    logger.add(log_file)

    logger.info("Starting fixed Dockerfile build process...")
    logger.info(f"Dataset path: {dataset_path}")
    logger.info(f"Repair method/mode/model: {args.method} / {args.mode} / {args.model} (slug={slug})")
    logger.info(f"Fixed Dockerfile base path: {fixed_dockerfile_base_path}")
    logger.info(f"Repo root: {repo_root}")
    logger.info(f"Output dir: {output_dir}")
    logger.info(f"Fix method tags: {fix_methods}")
    logger.info(f"Run mode: {run_mode}")
    logger.info(f"Max workers: {max_workers}")
    logger.info(f"Builder pool: {builder_pool}")
    logger.info(f"Prune every N builds: {prune_every_n_builds}")

    check_repos_status(repo_root)

    try:
        effective_workers = min(max_workers, len(builder_pool))
        processor = FixedDockerProcessor(
            dataset_path=dataset_path,
            fixed_dockerfile_base_path=fixed_dockerfile_base_path,
            fix_methods=fix_methods,
            repo_root=repo_root,
            output_dir=output_dir,
            max_workers=effective_workers,
            builder_pool=builder_pool,
            prune_every_n_builds=prune_every_n_builds,
            run_mode=run_mode,
            repair_model=args.model,
        )
        processor.process_all_repos_parallel()

    except Exception as e:
        import traceback
        logger.error(f"Error in main process: {e}, {traceback.format_exc()}")
        raise
    finally:
        logger.info("Fixed Dockerfile build process finished.")


if __name__ == "__main__":
    main()
