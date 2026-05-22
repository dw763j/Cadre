# Cadre: Context-aware drift repair framework

## Project Overview

This project studies **co-evolution of code changes and Dockerfiles**: when a codebase changes, the corresponding Dockerfile often needs to be updated in sync; otherwise Docker builds fail. The goal is to automatically repair Dockerfiles broken by code evolution using LLMs combined with build-dependency analysis.

---

> In the code repo, the method is named as "dofix", which is the implementation of "Cadre"


## Workflow Overview

```
GitHub GraphQL crawler produces repo list (results/500+_repos.json)
       ↓ get_info/whole_download_process.py
workflow_info → workflows → workflow_id_pair →
workflow_runs → workflow_logs → unzipped_workflow_logs →
failed_runs.json → failed_job_logs[_fixed_params]
       ↓ (in parallel) clone repos to cloned_repos/
       ↓ get_dataset/get_diff.py
failed_job_diffs[_fixed]/
       ↓ get_dataset/get_dataset.py
dataset_valid_fixed_params_with_dockerfile.json
       ↓
       ├── DoFix (main method)
       ├── Pure-LLM (baseline)
       ├── FlakiDock (baseline)
       └── Parfum (baseline)
       ↓
Run Docker Build to validate repairs
       ↓
Statistical analysis & visualization
```

---

## Data Collection Pipeline

In the paths below, `{out}` denotes the output root directory for the corresponding mode in `whole_download_process.py` (`test_results/` / `results/` / `results_multiple/`). Directories with `_fixed` / `_fixed_params` suffixes are regenerated using corrected parsing logic on top of older artifacts; the dataset currently uses the _fixed versions.


| Step                                               | Entry / Function                                                                                                  | Input                                                                                                     | Output                                                                                                        | Description                                                                                                                                                                                                                             |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Crawl repo list                                 | `utils/GitHubCrawler.py`, `utils/MultiLanguageCrawler.py`                                                         | GitHub GraphQL API                                                                                        | `results/500+_repos.json` (single mode), `results/crawled_repo_lists/**.json` (multiple mode aggregation)     | Filter by Stars, language, creation date per `config/crawler.yaml`                                                                                                                                                                      |
| 2. Fetch each repo's workflow list                 | `parallel_get_workflow_info`                                                                                      | repo URL list                                                                                             | `{out}/workflow_info/<owner>#<repo>#workflows.json`                                                           | Mainly to obtain workflow-id and workflow-path                                                                                                                                                                                          |
| 3. Download workflow yaml                          | `parallel_download_workflows`                                                                                     | `{out}/workflow_info/`                                                                                    | `{out}/workflows/<owner>#<repo>/<wf>.yml`                                                                     | Enables local static analysis of yaml files                                                                                                                                                                                             |
| 4. Pair workflows using docker/build-push-action   | `pair_workflow_file_and_id`                                                                                       | `{out}/workflow_info/` + `{out}/workflows/`                                                               | `{out}/workflow_id_pair.json`                                                                                 | Per-repo workflow_path ↔ workflow_id mapping; only workflows containing `docker/build-push-action` are kept                                                                                                                             |
| 5. Fetch all run records for workflows             | `parallel_get_workflow_runs_by_ids`                                                                               | `{out}/workflow_id_pair.json`                                                                             | `{out}/workflow_runs/<owner>#<repo>#runs.json`                                                                | Includes run_id, head_sha, conclusion, event, etc.                                                                                                                                                                                      |
| 6. Download detailed run log zip                   | `detail_workflow_logs_download`                                                                                   | `{out}/workflow_runs/`                                                                                    | `{out}/workflow_logs/<owner>#<repo>#<workflow_id>#<run_id>.zip`                                               | GitHub REST logs API                                                                                                                                                                                                                    |
| 7. Unzip detailed logs                             | `unzip_workflow_detail_logs`                                                                                      | `{out}/workflow_logs/`                                                                                    | `{out}/unzipped_workflow_logs/<owner>#<repo>#<workflow_id>#<run_id>/<N>_<job>.txt`                            | One txt per job                                                                                                                                                                                                                         |
| 8. Filter failed runs                              | `get_failed_runs`                                                                                                 | `{out}/workflow_runs/` + `{out}/unzipped_workflow_logs/`                                                  | `{out}/failed_runs.json`                                                                                      | Keep only runs with `conclusion == 'failure'` and existing logs; count failures by `event`                                                                                                                                              |
| 9. Split failure reason and build params from logs | `split_failure_reason_parallel`                                                                                   | `{out}/failed_runs.json` + `{out}/unzipped_workflow_logs/`                                                | `{out}/failed_job_logs[_fixed_params]/<run_folder>/<run_folder>#<idx>#fail_log.txt` + `...#build_params.json` | Parse `docker buildx build` command line for `build_params` (platform, file, context, build-arg, …) and extract failure snippet                                                                                                         |
| 10. Clone failed repos locally                     | `utils/RepoCloner.py` (currently commented out in `whole_download_process.py`; enable manually or run separately) | `<owner>#<repo>` from `{out}/failed_job_logs/`                                                            | `{out}/cloned_repos/<owner>#<repo>/`                                                                          | Shallow clone full repo for checkout of failed commit                                                                                                                                                                                   |
| 11. Compute diff for failed commit                 | `get_dataset/get_diff.py` (`save_diff_dataset`)                                                                   | `{out}/cloned_repos/` + `{out}/workflow_runs/` + `{out}/failed_job_logs/` + `{out}/workflow_id_pair.json` | `{out}/failed_job_diffs[_fixed]/<run_folder>.json`                                                            | Checkout parent of `head_sha`, diff changed files; also parse all docker/build-push-action params from yaml (including matrix expansion and include/exclude)                                                                            |
| 12. Aggregate final dataset                        | `get_dataset/get_dataset.py` (`get_dataset`)                                                                      | `failed_job_logs[_fixed_params]/` + `workflow_runs/` + `failed_job_diffs[_fixed]/` + `cloned_repos/`      | `{out}/dataset[_multiple]_valid_fixed_params_with_dockerfile.json`                                            | Each failed build gets `head_sha`, `build_params`, `error_log`, `diff_info`, `dockerfile_content`, `big_file`, etc.; builds with large base images like `nvidia/cuda`, `pytorch` are marked `big_file=True` to skip actual builds later |


> The `_fixed` / `_fixed_params` variants in steps 9 / 11 were regenerated after fixing `parse_docker_build_command` and matrix expansion logic; experiments use the fixed versions.

---

## Directory Structure

```
dockerfile-coevolution/
├── config/                  # Configuration files
│   ├── __init__.py          # All settings
│   ├── crawler.yaml         # GitHub crawler params (stars, language, date filters)
│   ├── buildkitd.toml       # BuildKit daemon config
│   └── .env.local           # Runtime secrets (LLM/GITHUB_TOKENS)
│
├── get_dataset/             # Data collection and dataset construction
│   ├── get_dataset.py       # Main entry: build dataset from failed builds
│   ├── get_diff.py          # Extract code diff between commits
│   ├── group_consecutive_failures.py  # Group consecutive failure records
│   └── analyze_runs.py      # Analyze workflow run statistics
│
├── methods/                 # Dockerfile repair methods
│   └── dofix/               # Main method DoFix
│       ├── standard_fix_process.py    # Main pipeline orchestration entry
│       ├── dofix.py                   # Two-step LLM repair core logic
│       ├── build_channel_analyzer.py  # Build dependency channel analyzer
│       ├── prompt_template.py         # LLM prompt template generation
│       ├── VariableResolver.py        # Dockerfile variable resolution
│       ├── PathManager.py             # Container path ↔ repo path mapping
│       └── build_systems/             # Per-language build system detectors
│           ├── manager.py             # Build system manager
│           ├── node.py / node_improved.py
│           ├── python.py
│           ├── go.py / go_tooling.py
│           ├── rust.py / rust_improved.py
│           ├── java.py
│           ├── csharp.py
│           ├── php.py / php_improved.py
│           ├── ruby.py
│           ├── make.py
│           └── cmake.py
│
├── build_dockerfile/        # Docker build execution and validation
│   └── build_fixed_docker.py  # Batch build repaired Dockerfiles
│
├── utils/                   # Shared utilities
│   ├── GitHubCrawler.py     # GitHub GraphQL API crawler
│   ├── MultiLanguageCrawler.py
│   ├── RepoCloner.py        # Repo cloning (with retries)
│   ├── GithubRepo.py        # GitPython wrapper
│   ├── openai.py            # OpenAI-compatible LLM API client
│   ├── utils.py             # LLM response parsing, token stats
│   └── check_repos.py       # Repo status checks
│
├── analyze_results/         # Result analysis and visualization
│   ├── cluster_results.py   # Success rate by cluster position
│   ├── comparable_results.py  # Cross-method comparison on same build set
│   └── upset_plot.py        # UpSet plot (multi-method overlap)
│
├── results/                 # Main experiment results (130 repos, 1040 builds)
└── results_multiple/        # Extended dataset (721 repos)
```

---

## Input Data

### Dataset Source

Repos are crawled via GitHub GraphQL API matching (see `config/crawler.yaml`):

- Stars ≥ 500
- Created after 2020-01-01
- GitHub Actions workflows using `docker/build-push-action`
- Languages: Python, C++, Java, C, C#, JavaScript, SQL, Go, Pascal
- Dockerfile at repo root

Two datasets:

- **single mode**: Stars ≥ 500 list above, saved to `results/500+_repos.json`, initially ~1766 repos
- **multiple mode**: relaxed criteria, crawled per language via `MultiLanguageCrawler`, aggregated to `results/crawled_repo_lists/final_results.json`, larger initial count

### Dataset Filtering Pipeline

From repo to usable build samples, several narrowing steps:

1. Pipeline download and parsing: any repo with at least one failed docker build gets a directory under `failed_job_logs[_fixed_params]/`
2. `get_dataset` further filters: **target `head_sha` checkoutable locally, `failed_job_diffs_fixed` exists, `build_params` parseable, Dockerfile readable**; qualifying entries go into `dataset_*_valid_fixed_params_with_dockerfile.json`
3. Dockerfile content matching `nvidia/tritonserver`, `nvidia/cuda`, `FROM pytorch`, `install torch`, `tensorflow`, `torchvision`, etc. is marked `big_file=True`; actual `docker build` is skipped later (image too large, build time uncontrolled)


| File                                                       | Location            | Scale                    | Description            |
| ---------------------------------------------------------- | ------------------- | ------------------------ | ---------------------- |
| `dataset_valid_fixed_params_with_dockerfile.json`          | `results/`          | 130 repos / 1,040 builds | Main dataset (~110 MB) |
| `dataset_multiple_valid_fixed_params_with_dockerfile.json` | `results_multiple/` | 721 repos                | Extended dataset       |


### Dataset Schema

```json
{
  "owner#repo": {
    "owner#repo#workflow_id#run_id#build_index": {
      "head_sha": "Commit hash at failure",
      "run_folder": "Absolute path to failed_job_logs_fixed_params/<folder>",
      "build_params": {
        "platform": ["linux/amd64"],
        "file": "Dockerfile",
        "context": ".",
        "build-arg": {},
        "target": ""
      },
      "build_params_path": "Absolute path to corresponding build_params.json",
      "error_log": "Build failure log",
      "error_log_path": "Absolute path to corresponding fail_log.txt",
      "dockerfile_content": "Dockerfile text read after checkout to head_sha",
      "diff_info": {
        "<file_path>": {
          "diff_content": "...",
          "file_status": "modified|added|deleted",
          "old_path": "...",
          "new_path": "..."
        }
      },
      "big_file": false
    }
  }
}
```

### Auxiliary Data (under `{out}/`)


| Directory                       | Contents                                                           |
| ------------------------------- | ------------------------------------------------------------------ |
| `cloned_repos/`                 | Cloned Git repos, directory name `owner#repo`                      |
| `failed_job_logs_fixed_params/` | Failure snippets and parsed build params, one subdirectory per run |
| `failed_job_diffs_fixed/`       | Per-run commit diff + docker-build-push-action params              |


---

## Actual Experiment Path Examples

With repo root as CWD, single mode (`mode='single'` in `whole_download_process.py`, `base_dir='results'` in `get_dataset.py`) produces:

```
results/500+_repos.json                                      # Crawl result for 1766 repos (raw input)
results/workflow_info/                                       # Workflow lists for 1763 repos
results/workflows/                                           # Workflow yaml for 287 repos
results/workflow_id_pair.json                                # Workflow mapping with docker/build-push-action
results/workflow_runs/                                       # runs.json for 652 repos
results/workflow_logs/                                       # Downloaded run detail log zips
results/unzipped_workflow_logs/                              # Unzipped logs for 14,948 runs
results/failed_runs.json                                     # Filtered failed run list
results/failed_job_logs_fixed_params/                        # fail_log.txt + build_params.json for 1577 failed runs
results/cloned_repos/                                        # 135 locally cloned repos
results/failed_job_diffs_fixed/                              # Diff + docker params for 742 runs
results/dataset_valid_fixed_params_with_dockerfile.json      # Main dataset: 130 repos / 1,040 builds
```

Run commands (consistent with script header comments):

```bash
# Data download and split pipeline (steps 1–9; step 10 RepoCloner is commented by default—enable manually or run separately)
python -m get_info.whole_download_process

# Diff for failed commits
python -m get_dataset.get_diff

# Final dataset aggregation
find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + \
    && python -m get_dataset.get_dataset
```

> Note: `get_dataset.py` / `get_diff.py` / `split_failure_reason.py` switch `results` vs `results_multiple` via the top-level `base_dir` variable; they are independent and do not mix.

---

## Repair Methods

All repair methods consume the same dataset `results/dataset_valid_fixed_params_with_dockerfile.json`. Default output directories are derived from `results_fixed_dockerfiles_dir` / `results_repairing_dockerfile_dir` in `config` (aligned with `standard_fix_process` / `build_fixed_docker`):

- **DoFix / Pure-LLM (and general methods)**: `results/fixed_dockerfiles/<method>/<mode>/<model_slug>/` and `results/repairing_dockerfile/<method>/<mode>/<model_slug>/`. `<model_slug>` is the model name with `/` replaced by `_`.
- **Parfum**: no mode / model subdirs; only `results/fixed_dockerfiles/parfum/` and `results/repairing_dockerfile/parfum/`.
- Repaired Dockerfile per file: `…/<dirs above>/<full_build_id>#<method>#<model_slug>` (`/` in filenames replaced by `_`).
- Repair process logs: `…/repair_results_<method>/<owner>#<repo>.json` + `…/prompt_results_<method>/<full_build_id>#<method>#<model>.log` (relative to repairing root above).

`<full_build_id>` format: `<owner>#<repo>#<workflow_id>#<run_id>#<build_index>`.

### DoFix / Pure-LLM / Parfum (unified entry)

**Entry:** `methods/dofix/standard_fix_process.py`, configured via CLI `--method` / `--mode` / `--model` (and optional `--project-root`, `--fixed-dockerfile-base-path`, `--run-logs-dir`):

#### Patch dockerfile_parse package

Patch dockerfile_parse package

Note: the stock Dockerfile parser rejects files not named Dockerfile. Patch as follows:
In `.venv/lib/python3.12/site-packages/dockerfile_parse/parser.py`:

```python
class DockerfileParser(object):
    def __init__(self, path=None,
                 cache_content=False,
                 env_replace=True,
                 parent_env=None,
                 fileobj=None,
                 build_args=None):
        """
        ...
        """

        self.fileobj = fileobj

        if self.fileobj is not None:
            if path is not None:
                raise ValueError("Parameters path and fileobj cannot be used together.")
            else:
                self.fileobj.seek(0)
        else:
            path = path or '.'
            if path.endswith(DOCKERFILE_FILENAME):
                self.dockerfile_path = path
            elif os.path.isfile(path):       # change here
                self.dockerfile_path = path  # change here
            else:
                self.dockerfile_path = os.path.join(path, DOCKERFILE_FILENAME)
# method: dofix | pure-llm | parfum; mode: standard | remove_build_channel | remove_key_files (parfum ignores)
# model: DeepSeek-V3 / gpt-5 etc. (parfum ignores)
find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + \
    && python -m methods.dofix.standard_fix_process --method dofix --mode standard --model DeepSeek-V3
```

- **DoFix (main method, mode=standard)**: two-step LLM repair.
  - ① `BuildChannelAnalyzer` parses Dockerfile, producing dependency files per RUN and cross-stage links;
  - ② LLM selects "key files" from build channels + diff;
  - ③ LLM generates repair from key files + error log + original Dockerfile.
- **DoFix-NoChannel (ablation, mode=remove_build_channel)**
  - Skip `BuildChannelAnalyzer`; keep remaining two LLM steps.
- **DoFix-NoKeyFiles (ablation, mode=remove_key_files)**
  - Keep `BuildChannelAnalyzer`; skip key-file LLM step; feed full diff as code_changes to repair prompt.
- **Pure-LLM (method=pure-llm)**
  - No analysis or retrieval; single LLM step with `dockerfile + error_log + diff`.
- **Parfum (method=parfum)**
  - Runs `docker-parfum repair --stdin <dockerfile> -o <repaired>` (rule-based); output path has no mode/model subdirs.

**Supported build systems (DoFix BuildChannelAnalyzer only)**

- Node.js (npm/yarn/pnpm)
- Python (pip/poetry)
- Go
- Rust (cargo)
- Java (Maven/Gradle)
- C# (.NET)
- PHP
- Ruby (bundler)
- Make
- CMake

**Input / output:**


| Data                 | Path                                                                                                    |
| -------------------- | ------------------------------------------------------------------------------------------------------- |
| Dataset              | `results/dataset_valid_fixed_params_with_dockerfile.json`                                               |
| Local repos          | `results/cloned_repos/<owner>#<repo>/`                                                                  |
| Repaired Dockerfiles | `results/fixed_dockerfiles/<method>/<mode>/<model_slug>/` (Parfum: `…/fixed_dockerfiles/parfum/`)       |
| Run logs             | `results/repairing_dockerfile/<method>/<mode>/<model_slug>/` (Parfum: `…/repairing_dockerfile/parfum/`) |
| LLM tokens           | `config.LLM_API_KEY` (from `.env.local`)                                                                |


### FlakiDock (baseline)

**Entry:** `methods/baselines/docker_flakiness_study/error_analysis/error_repair/flakiDock/modified_main.py`

This subdirectory is forked from the original repo; imports use relative package names `from llm_apis ...` / `from error_repair ...`, so it cannot be run with `python -m` from the repo root—cd to its package root:

```bash
cd methods/baselines/docker_flakiness_study/error_analysis
python -m error_repair.flakiDock.modified_main
```

Its `__main__` uses hardcoded absolute paths and `openai` base_url/api_key; edit for your environment (TODO: wire to `config.LLM_API_KEY`). Current hardcoded values:

- Dataset: `results/dataset_valid_fixed_params.json` (**note: missing** `_with_dockerfile`**, unlike DoFix dataset—only build_params, no dockerfile_content field; should not matter since it only uses Dockerfile as input**)
- Repaired Dockerfiles: `results/fixed_dockerfiles_flakidock_upgrade_response_deal_DeepSeek-V3/`
- Run logs: `results/repairing_dockerfile/flakidock_upgrade_response_deal_DeepSeek-V3/`

### Shipwright (baseline, not wired to main pipeline)

`methods/baselines/shipwright/` keeps the original repo and `standalone/shipwright.sh`, but the `fix_method == 'shipwright'` branch in `build_fixed_docker.py` is currently `pass` (not integrated); run per `methods/baselines/shipwright/README.md` if needed.

---

## Docker Build Validation

**Entry:** `build_dockerfile/build_fixed_docker.py`

```bash
# Start BuildKit / local registry once
./setup_docker_builders.sh

# Minimal: default config paths for dofix#DeepSeek-V3
find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + \
    && python -m build_dockerfile.build_fixed_docker

# Specify method and paths (multiple fix_methods allowed; Dockerfiles for each tag must resolve under base_path)
python -m build_dockerfile.build_fixed_docker --method dofix --mode standard --model DeepSeek-V3

# All options
python -m build_dockerfile.build_fixed_docker --help
```

Common constants from `config/__init__.py`; **repaired Dockerfile / build output roots** are derived from `--method` / `--mode` / `--model` (and Parfum exception) via `results_fixed_dockerfiles_dir` / `results_fixed_docker_builds_dir`; override with `--fixed_dockerfile_base_path` / `--output_dir`.


| Variable                         | Value                                                                            | Meaning                               |
| -------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------- |
| `PROJECT_ROOT`                   | (this repo root)                                                                 | Auto-resolved in `config/__init__.py` |
| `MAX_BUILDERS`                   | 4                                                                                | Parallel Docker buildx builders       |
| `REGISTRY_PORT`                  | 5001                                                                             | Local registry port                   |
| `DEFAULT_DATASET_PATH`           | `results/dataset_valid_fixed_params_with_dockerfile.json`                        | Dataset                               |
| `CLONED_REPOS_DIR`               | `results/cloned_repos`                                                           | Repo root                             |
| Default repaired Dockerfile root | `results/fixed_dockerfiles/<method>/<mode>/<slug>/` (Parfum: two fewer levels)   | Same as `build_fixed_docker`          |
| Default build output root        | `results/fixed_docker_builds/<method>/<mode>/<slug>/` (Parfum: two fewer levels) | Same as above                         |


`--fix_methods` selects which methods to run (multiple values, space-separated). Current dispatcher supports:

- `dofix#DeepSeek-V3`, `pure-llm#DeepSeek-V3`, `parfum`, `flakidock#DeepSeek-V3`

Filenames must match `<fixed_dockerfile_base_path>/<full_build_id>#<fix_method>`; unknown values log a warning but matching is still attempted.

**Output directory:** `<output_dir>/`

```
<output_dir>/
├── build_logs/build#<full_build_id>#<fix_method>.log    # Raw log per docker build
├── run_logs/<owner>#<repo>.json                         # Per-repo build results (supports resume)
└── results/fixed_docker_build_summary_<timestamp>.json  # Global summary
```

Summary JSON example:

```json
{
  "timestamp": "20250910_055539",
  "total_repos": 130,
  "repos_processed": 130,
  "total_builds": 660,
  "successful_builds": 203,
  "failed_builds": 421,
  "skipped_builds": 380,
  "timeout_builds": 36
}
```

---

## Experiment Group Comparison


| Experiment group     | method × mode                | Model                        | Description                                                   |
| -------------------- | ---------------------------- | ---------------------------- | ------------------------------------------------------------- |
| **DoFix** (full)     | dofix × standard             | DeepSeek-V3                  | Main method: BuildChannelAnalyzer + key-file selection        |
| **DoFix-NoChannel**  | dofix × remove_build_channel | DeepSeek-V3                  | Ablation: remove BuildChannelAnalyzer                         |
| **DoFix-NoKeyFiles** | dofix × remove_key_files     | DeepSeek-V3                  | Ablation: remove key-file selection step                      |
| **Pure-LLM**         | pure-llm × standard          | DeepSeek-V3                  | Ablation: no build-dependency analysis, no key-file selection |
| **FlakiDock**        | flakidock (separate entry)   | GPT-4 / DeepSeek-V3          | Existing tool baseline                                        |
| **Parfum**           | parfum                       | None (dir has no mode/model) | Rule-based baseline                                           |


---

## Full Experiment Run Order

Example: **DoFix (DeepSeek-V3)** from scratch to build validation:

```bash
# 0. Prepare .env.local (LLM_API_KEY / GITHUB_TOKENS)
#    config/__init__.py loads it automatically

# 1. Crawl repo list (if not already done)
#    utils/GitHubCrawler.py or utils/MultiLanguageCrawler.py

# 2. Data download and split pipeline
python -m get_info.whole_download_process          # steps 2–9
python -m get_dataset.get_diff                     # step 11
find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + \
    && python -m get_dataset.get_dataset           # step 12

# 3. Generate repaired Dockerfiles (example: DoFix-NoChannel + DeepSeek-V3)
find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + \
    && python -m methods.dofix.standard_fix_process \
        --method dofix --mode remove_build_channel --model DeepSeek-V3

# 4. Docker build validation
./setup_docker_builders.sh
#    Default paths from --method / --mode / --model; or explicit paths below
find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + \
    && python -m build_dockerfile.build_fixed_docker --method dofix --mode standard --model DeepSeek-V3
./teardown_docker_builders.sh  # cleanup after run

# 5. Metrics and visualization (see next section)
```

Parfum / Pure-LLM: step 3 with different `method`, then step 4 for builds. FlakiDock step 3 uses separate entry (see "FlakiDock (baseline)" above).

---

## Runtime Environment and Configuration

`config/__init__.py` is the single config entry point for path constants, env vars, and helpers:

- Path constants and helpers: `PROJECT_ROOT`, `RESULTS_DIR`, `CLONED_REPOS_DIR`, `DEFAULT_DATASET_PATH`, `slug_model`, `results_fixed_dockerfiles_dir`, `results_fixed_docker_builds_dir`, `results_repairing_dockerfile_dir`, etc.
- Docker build params: `MAX_BUILDERS` (4), `REGISTRY_PORT` (5001)
- Env vars (auto-loaded from `.env.local`): `GITHUB_TOKENS`, `LLM_API_KEY` / `LLM_API_BASE` / `LLM_MODEL`, optional `GITHUB_PROXY`, optional `LEGACY_ABSOLUTE_ROOTS` (comma-separated, for migrating old dataset paths)
- Helpers: `env_get`, `env_get_list`, `resolve_path`, `init_dir`

`**.env.local` template:** copy from `.env.example`, then edit:

```bash
GITHUB_TOKENS=ghp_xxx,ghp_yyy        # comma-separated, multiple tokens for rotation
LLM_API_KEY=sk-yyy                    # OpenAI-compatible API key (DoFix / Pure-LLM / classification)
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
# GITHUB_PROXY=https://ghproxy.com    # optional mirror prefix for clone/raw GitHub URLs
```

**Dependencies:**

- Python 3.10+ (`match/case` and `dict | None` annotations)
- `dockerfile-parse`, `GitPython`, `tiktoken`, `loguru`, `PyYAML`, `tqdm`
- Docker + BuildKit, local registry (see `setup_docker_builders.sh`)
- Optional: `docker-parfum` (Parfum baseline), `upsetplot` + `venn` (visualization)

---

## Analysis and Visualization Scripts

### Metrics and failure classification (`methods/analysis/`)

```bash
# SPV / CRS / PMS metrics (Static Patch Validity, Context Relevance Score, Patch Minimality Score)
python -m methods.analysis.compute_metrics \
    --repair_dir results/repairing_dockerfile/dofix/standard/DeepSeek-V3/repair_results_dofix \
    --dataset    results/dataset_valid_fixed_params_with_dockerfile.json \
    --output     results/metrics_dofix_standard.json

# Classify failure logs per paper Table 1 (uses LLM_MODEL)
python -m methods.analysis.classify_failures \
    --logs_dir  results/failed_job_logs_fixed_params \
    --dataset   results/dataset_valid_fixed_params_with_dockerfile.json \
    --output    results/failure_classification.json \
    --max_cases -1
```

### Success rate comparison and plots (`analyze_results/`)


| Script                                  | Input                                                                       | Output                                        | Purpose                                                            |
| --------------------------------------- | --------------------------------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------ |
| `analyze_results/comparable_results.py` | Multiple `…/fixed_docker_builds/.../run_logs/` (or legacy flat `run_logs/`) | Dict (used by upset_plot.py)                  | Cross-tool comparison on same build set with one tool as reference |
| `analyze_results/upset_plot.py`         | Output above                                                                | `plots/venn_*.pdf` + `plots/upset_plot_*.pdf` | UpSet / Venn plots of per-method success set overlap               |
| `analyze_results/cluster_results.py`    | `results/failed_job_clusters/` + each `run_logs/`                           | `plots/cluster_success_by_position.pdf`       | Success rate bucketed by build position in failure cluster         |


> `cluster_results.py` infers tool id from `run_logs` under hierarchical layout: `{method}_{mode}_{slug}` (Parfum: `parfum`); default `SELECTED_TOOLS` matches. For legacy flat dirs or custom FlakiDock paths, use `--tool_run_log_roots` / `--selected_tools`. `upset_plot.py` default mapping is similar; use `--result_paths NAME=PATH` if different. PDFs default to `plots/`.

```bash
# Default args (reads results/ablation/ and results/)
python -m analyze_results.upset_plot

# Custom run_logs mapping
python -m analyze_results.upset_plot \
    --compare_tool DoFix-V3 \
    --plots_dir plots \
    --result_paths \
        "DoFix-V3=results/fixed_docker_builds/dofix/standard/DeepSeek-V3/run_logs" \
        "Parfum=results/fixed_docker_builds/parfum/run_logs" \
        "FlakiDock-GPT4=results/fixed_docker_builds_flakidock_gpt_4/run_logs"

# Success rate by cluster position (produce failed_job_clusters/ first if missing)
python -m analyze_results.cluster_results \
    --clusters_dir results/failed_job_clusters \
    --tool_run_log_roots results/ablation results \
    --compare_tool dofix_standard_DeepSeek-V3 \
    --output plots/cluster_success_by_position.pdf
```