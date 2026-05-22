"""
Classify Dockerfile build failure logs using an LLM, following the taxonomy
defined in Table 1 of the paper (Cadre / D3 dataset).

Reads from:  results/dataset_valid_fixed_params_with_dockerfile_relative.json
             the "error_log" field per build_id entry (full build failure log).
             Optional: --logs_dir falls back to fail_log.txt when error_log is empty.

Writes to:   results/failure_classification.json  (and a summary JSON)

Usage:
    # From project root: activate venv first (repo convention)
    source .venv/bin/activate
    python -m methods.analysis.classify_failures \
        --dataset   results/dataset_valid_fixed_params_with_dockerfile_relative.json \
        --output    results/failure_classification.json \
        [--logs_dir  results/failed_job_logs_fixed_params]  # fallback only when error_log is empty
        [--model    DeepSeek-V3] \
        [--max_cases 200]        # set to -1 for all
        [--workers 8]            # LLM concurrency (1 = serial)
        [--resume]               # skip already-classified entries
        [--log_file logs/classify_failures.log]

    # Subset mode: keep only build ids present in a relative-path subset dataset (no LLM calls)
    source .venv/bin/activate
    python -m methods.analysis.classify_failures \
        --filter_subset \
        --classification_input results/failure_classification.json \
        --subset_dataset results/dataset_valid_fixed_params_with_dockerfile_relative.json \
        [--output results/failure_classification_relative_subset.json]
"""

# Run: source .venv/bin/activate && python -m methods.analysis.classify_failures --help
import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.openai import call_model, llm_output_to_json
from config import LLM_API_KEY, LLM_API_BASE, LLM_MODEL


class ClassificationError(Exception):
    """Raised when classification pipeline fails at a critical stage."""


# ---------------------------------------------------------------------------
# Taxonomy (matches Table 1 in the paper)
# ---------------------------------------------------------------------------

TAXONOMY = {
    "APP_NPM":        "npm: Build/install/CI failures (npm run build, ci, install, etc.)",
    "APP_UV":         "uv: Python package manager sync & operational errors",
    "APP_GO":         "Go: Build/install/dependency download failures (go build, install, mod)",
    "APP_PNPM":       "pnpm: Build failures",
    "APP_PIP":        "pip: Package installation errors (version conflicts, not found)",
    "APP_PYPACKAGE":  "Python package build failures (during pip install, setup.py, wheel)",
    "APP_MAKE":       "Makefile: Execution failures",
    "APP_OTHER":      "Other build systems (yarn, poetry, task, gradle, maven, cargo, etc.)",
    "SYS_MISSING":    "Missing system dependencies (gcc, ffmpeg, libssl, etc.)",
    "SYS_PKG":        "OS package manager errors (apt-get, apk add failures)",
    "SYS_PYVER":      "Python version incompatibility",
    "SYS_DOWNLOAD":   "General file download errors (wget, curl failures)",
    "SCRIPT_CUSTOM":  "Developer custom script failure (custom shell script, entrypoint, etc.)",
    "SCRIPT_SYNTAX":  "RUN command syntax error",
    "DOCKER_IMAGE":   "Base image error (not found, auth error, invalid tag)",
    "DOCKER_COPY":    "File copy/add failure (COPY, ADD)",
    "INFRA_PUSH":     "Docker image push/upload failure",
    "INFRA_DISK":     "Insufficient disk space during image export/build",
    "INFRA_CACHE":    "CI cache interaction error",
    "UNFIXABLE":      "Build failure unrelated to Dockerfile (software logic bug, test failure, etc.)",
    "UNKNOWN":        "Cannot determine cause from the log (general error codes, no useful output)",
}

TAXONOMY_LIST = "\n".join(f"  {k}: {v}" for k, v in TAXONOMY.items())

SYSTEM_PROMPT = f"""You are an expert in Docker build failures and CI/CD pipelines.
Classify the given Docker build failure log into exactly ONE of the following categories:

{TAXONOMY_LIST}

Reply with a JSON object containing:
  "category": <one of the category codes above>,
  "confidence": <"high"|"medium"|"low">,
  "reason": <one-sentence explanation>

Only output valid JSON. No markdown fences."""


def build_classify_prompt(fail_log: str, max_log_chars: int = 3000) -> str:
    truncated = fail_log.strip()[-max_log_chars:] if len(fail_log) > max_log_chars else fail_log.strip()
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Docker build failure log:\n\n{truncated}\n\n"
        f"Classify this failure."
    )


# ---------------------------------------------------------------------------
# Log discovery
# ---------------------------------------------------------------------------

def extract_error_logs_by_build_id(raw_dataset: dict) -> dict[str, str]:
    """Walk dataset repo -> builds and collect build_id -> error_log text (missing/non-string treated as empty)."""
    out: dict[str, str] = {}
    for builds in raw_dataset.values():
        if not isinstance(builds, dict):
            continue
        for bid, info in builds.items():
            if not isinstance(info, dict):
                out[bid] = ""
                continue
            raw = info.get("error_log")
            if raw is None:
                out[bid] = ""
            elif isinstance(raw, str):
                out[bid] = raw
            else:
                out[bid] = str(raw)
    return out


def discover_cases(logs_dir: str) -> dict:
    """
    Returns dict: full_build_id -> fail_log_path
    Each case dir is like: <repo>#<wf_id>#<run_id>/
    Inside there may be multiple log files: <case>#<n>#fail_log.txt
    We pick the first (index 0) fail_log per case directory.
    """
    root = Path(logs_dir)
    if not root.exists():
        raise FileNotFoundError(f"logs_dir does not exist: {logs_dir}")
    if not root.is_dir():
        raise NotADirectoryError(f"logs_dir is not a directory: {logs_dir}")

    cases = {}
    for case_dir in sorted(root.iterdir()):
        if not case_dir.is_dir():
            continue
        case_id = case_dir.name
        # Find all fail_log.txt files; pick the one with smallest index
        log_files = sorted(case_dir.glob("*#fail_log.txt"))
        if log_files:
            cases[case_id] = str(log_files[0])
    return cases


def setup_logger(log_file: str, level: str = "INFO") -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        log_file,
        level=level,
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )
    logger.add(sys.stderr, level=level)


def load_json_file(path: str) -> dict:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"JSON file does not exist: {path}")
    try:
        with open(file_path, encoding="utf-8") as f:
            content = json.load(f)
    except json.JSONDecodeError as exc:
        raise ClassificationError(f"Invalid JSON format in {path}: {exc}") from exc
    except OSError as exc:
        raise ClassificationError(f"Failed to read JSON file {path}: {exc}") from exc

    if not isinstance(content, dict):
        raise ClassificationError(f"Expected top-level JSON object in {path}")
    return content


def build_summary(results: dict) -> dict:
    from collections import Counter

    category_counts = Counter(v.get("category", "UNKNOWN") for v in results.values())
    return {
        "total_classified": len(results),
        "category_counts": {
            cat: {
                "count": count,
                "description": TAXONOMY.get(cat, ""),
            }
            for cat, count in sorted(category_counts.items(), key=lambda x: -x[1])
        },
    }


def collect_build_ids_from_dataset(raw_dataset: dict) -> set[str]:
    """Same traversal as run() dataset_lookup; collect all build ids."""
    ids: set[str] = set()
    for builds in raw_dataset.values():
        if not isinstance(builds, dict):
            continue
        for bid in builds:
            ids.add(bid)
    return ids


def run_filter_subset(
    classification_path: str,
    subset_dataset_path: str,
    output_path: str,
) -> None:
    """Keep classification entries whose keys appear in subset_dataset; write to output_path and *_summary.json."""
    classification = load_json_file(classification_path)
    subset = load_json_file(subset_dataset_path)
    allowed = collect_build_ids_from_dataset(subset)
    filtered = {k: v for k, v in classification.items() if k in allowed}
    missing_in_classification = allowed - filtered.keys()
    if missing_in_classification:
        logger.info(
            f"Subset has {len(missing_in_classification)} build ids not present in classification "
            f"(omitted from output)."
        )
    _save(filtered, output_path)
    summary = build_summary(filtered)
    summary["source_classification"] = str(classification_path)
    summary["subset_dataset"] = str(subset_dataset_path)
    summary["input_entries"] = len(classification)
    summary["subset_build_ids"] = len(allowed)
    summary["kept_entries"] = len(filtered)
    summary_path = str(Path(output_path).with_name(f"{Path(output_path).stem}_summary.json"))
    _save(summary, summary_path)
    logger.info(
        f"Filter subset: kept {len(filtered)}/{len(classification)} from classification, "
        f"subset has {len(allowed)} build ids; wrote {output_path} and {summary_path}"
    )


def parse_models(model_value: str) -> list[str]:
    models = [item.strip() for item in model_value.split(",") if item.strip()]
    if not models:
        raise ClassificationError("No valid model found in --model / LLM_MODEL")
    return models


def classify_with_model_rotation(
    prompt: str,
    case_id: str,
    models: list[str],
    retry_per_model: int = 3,
) -> tuple[dict | None, str | None]:
    for model_name in models:
        logger.info(f"Case {case_id}: trying model {model_name}")
        for attempt in range(retry_per_model):
            try:
                resp = call_model(
                    token=LLM_API_KEY,
                    api_address=LLM_API_BASE,
                    model=model_name,
                    message=prompt,
                    max_retries=1,
                )
                content = resp.get("content", "")
                if not content:
                    error_type = resp.get("error_type")
                    error_message = resp.get("error_message")
                    if error_type:
                        raise ValueError(f"empty response ({error_type}: {error_message})")
                    raise ValueError("empty response")

                parsed = llm_output_to_json(content)
                if parsed is None:
                    raise ValueError(f"JSON parse failed: {content[:200]}")
                if not isinstance(parsed, dict):
                    raise ValueError("model output must parse to a JSON object")
                if parsed.get("category") not in TAXONOMY:
                    parsed["category"] = "UNKNOWN"
                if parsed.get("confidence") not in {"high", "medium", "low"}:
                    parsed["confidence"] = "low"
                parsed.setdefault("reason", "No reason provided by model")
                parsed["model"] = model_name
                return parsed, None
            except Exception as exc:
                logger.warning(
                    f"Case {case_id}: model {model_name} attempt {attempt+1}/{retry_per_model} failed: {exc}"
                )
                if attempt < retry_per_model - 1:
                    time.sleep(2 ** attempt)

    return None, f"All models failed after retries: {', '.join(models)}"


def _classify_one_job(
    case_id: str,
    fail_log: str,
    models: list[str],
) -> tuple[str, dict, bool]:
    """Single classification job for the thread pool. Returns (case_id, result dict, whether LLM parse succeeded this round)."""
    prompt = build_classify_prompt(fail_log)
    parsed, error_message = classify_with_model_rotation(
        prompt=prompt,
        case_id=case_id,
        models=models,
        retry_per_model=3,
    )
    if parsed is not None:
        return case_id, parsed, True
    return (
        case_id,
        {
            "category": "UNKNOWN",
            "confidence": "low",
            "reason": f"Classification failed: {error_message}",
            "model": ",".join(models),
        },
        False,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    dataset_path: str,
    output_path: str,
    logs_dir: str | None = None,
    model: str = LLM_MODEL,
    max_cases: int = -1,
    resume: bool = True,
    workers: int = 4,
):
    raw_dataset = load_json_file(dataset_path)
    case_logs = extract_error_logs_by_build_id(raw_dataset)
    logger.info(
        f"Loaded {len(case_logs)} build ids from dataset {dataset_path} (error_log field)"
    )

    disk_cases: dict[str, str] = {}
    if logs_dir:
        try:
            disk_cases = discover_cases(logs_dir)
            logger.info(
                f"Optional logs_dir {logs_dir}: {len(disk_cases)} directories with fail_log.txt "
                "(used only when error_log is empty)"
            )
        except FileNotFoundError:
            logger.warning(f"logs_dir does not exist, skipping file fallback: {logs_dir}")
        except NotADirectoryError:
            logger.warning(f"logs_dir is not a directory, skipping file fallback: {logs_dir}")

    if workers < 1:
        raise ClassificationError("workers must be >= 1")

    model_list = parse_models(model)
    logger.info(f"Using model rotation list: {model_list}, workers={workers}")

    # Load existing results if resuming
    existing: dict = {}
    if resume and Path(output_path).exists():
        existing = load_json_file(output_path)
        logger.info(f"Resuming: {len(existing)} cases already classified")

    case_ids = sorted(case_logs.keys())
    if max_cases > 0:
        case_ids = case_ids[:max_cases]

    results = dict(existing)
    new_count = 0

    def _resolve_fail_log(case_id: str) -> str:
        fail_log = case_logs.get(case_id, "") or ""
        if not fail_log.strip() and disk_cases and case_id in disk_cases:
            log_path = disk_cases[case_id]
            try:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    fail_log = f.read()
            except OSError as exc:
                logger.warning(
                    f"Failed to read fallback log file for {case_id}: {log_path}, error: {exc}"
                )
        return fail_log

    pending: list[tuple[str, str]] = []
    for i, case_id in enumerate(case_ids):
        logger.info(f"Processing case {i+1}/{len(case_ids)}: {case_id}")
        if case_id in results:
            logger.info(f"Skip case {i+1}/{len(case_ids)} (already classified): {case_id}")
            continue

        fail_log = _resolve_fail_log(case_id)
        if not fail_log.strip():
            results[case_id] = {
                "category": "UNKNOWN",
                "confidence": "low",
                "reason": "No log content available",
                "source": "no_error_log",
            }
            continue

        pending.append((case_id, fail_log))

    result_lock = threading.Lock()
    completed = 0
    if pending:
        logger.info(f"Submitting {len(pending)} cases to thread pool (max_workers={workers})")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_cid = {
                executor.submit(_classify_one_job, cid, fl, model_list): cid
                for cid, fl in pending
            }
            for fut in as_completed(future_to_cid):
                cid = future_to_cid[fut]
                try:
                    case_id, record, llm_ok = fut.result()
                except Exception as exc:
                    logger.exception(f"Case {cid}: unexpected worker failure: {exc}")
                    case_id = cid
                    record = {
                        "category": "UNKNOWN",
                        "confidence": "low",
                        "reason": f"Worker error: {exc}",
                        "model": ",".join(model_list),
                    }
                    llm_ok = False
                with result_lock:
                    results[case_id] = record
                    if llm_ok:
                        new_count += 1
                    completed += 1
                    if completed % 5 == 0:
                        _save(results, output_path)
                        logger.info(
                            f"Checkpoint: {completed}/{len(pending)} classified, "
                            f"{new_count} new LLM successes"
                        )

    _save(results, output_path)
    summary = build_summary(results)
    summary["new_this_run"] = new_count

    summary_path = str(Path(output_path).with_name(f"{Path(output_path).stem}_summary.json"))
    _save(summary, summary_path)
    logger.info(f"Classification complete: total={len(results)}, new={new_count}")
    logger.info(f"Saved classification result to: {output_path}")
    logger.info(f"Saved summary JSON to: {summary_path}")


def _save(results: dict, path: str):
    try:
        os.makedirs(Path(path).parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        raise ClassificationError(f"Failed to save JSON file {path}: {exc}") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify Dockerfile failure logs with LLM")
    parser.add_argument(
        "--logs_dir",
        default=None,
        help="Optional: when error_log is empty in the dataset, read fail_log.txt from this directory by build_id",
    )
    parser.add_argument(
        "--dataset",
        default="results/dataset_valid_fixed_params_with_dockerfile_relative.json",
        help="Dataset JSON: read error_log per build_id as classification input",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Classification mode default results/failure_classification.json; subset mode default "
        "results/failure_classification_relative_subset.json",
    )
    parser.add_argument("--model",     default=LLM_MODEL)
    parser.add_argument("--max_cases", type=int, default=-1, help="-1 for all")
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent LLM worker threads (1 = serial; watch API rate limits)",
    )
    parser.add_argument("--no_resume", action="store_true")
    parser.add_argument("--log_file",  default="logs/classify_failures.log")
    parser.add_argument("--log_level", default="INFO")
    parser.add_argument(
        "--filter_subset",
        action="store_true",
        help="Subset mode: keep build ids from full classification JSON that appear in subset dataset (--output, no LLM)",
    )
    parser.add_argument(
        "--classification_input",
        default="results/failure_classification.json",
        help="With --filter_subset: path to full classification results",
    )
    parser.add_argument(
        "--subset_dataset",
        default="results/dataset_valid_fixed_params_with_dockerfile_relative.json",
        help="With --filter_subset: subset dataset JSON defining which build ids to keep",
    )
    args = parser.parse_args()

    output_path = args.output
    if output_path is None:
        output_path = (
            "results/failure_classification_relative_subset.json"
            if args.filter_subset
            else "results/failure_classification.json"
        )

    setup_logger(args.log_file, args.log_level.upper())
    try:
        if args.filter_subset:
            run_filter_subset(
                classification_path=args.classification_input,
                subset_dataset_path=args.subset_dataset,
                output_path=output_path,
            )
        else:
            run(
                dataset_path=args.dataset,
                output_path=output_path,
                logs_dir=args.logs_dir,
                model=args.model,
                max_cases=args.max_cases,
                resume=not args.no_resume,
                workers=args.workers,
            )
    except Exception as exc:
        logger.exception(f"Fatal error during classification: {exc}")
        raise SystemExit(1) from exc
