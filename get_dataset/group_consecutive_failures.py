import os
import re
import json
from pathlib import Path
from typing import Any
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from loguru import logger


@dataclass
class RunRef:
    run_id: str
    workflow_id: str | None
    created_at: str | None
    updated_at: str | None
    conclusion: str | None
    attempt: str | None
    log_path: str | None


def parse_repo_key_from_filename(filename: str) -> str:
    return filename.replace('#runs.json', '')


def parse_failed_log_filename(file_name: str) -> tuple[str, str, str, str, str] | None:
    """
    Naming: owner#repo#workflowid#workflow_run_id#attempt#fail_log.txt
    Returns (owner#repo, workflow_id, run_id, attempt, stem)
    """
    if not file_name.endswith('#fail_log.txt'):
        return None
    stem = file_name[:-len('#fail_log.txt')]
    parts = stem.split('#')
    if len(parts) < 5:
        return None
    owner_repo = f"{parts[0]}#{parts[1]}"
    workflow_id = parts[2]
    run_id = parts[3]
    attempt = parts[4]
    return owner_repo, workflow_id, run_id, attempt, stem


def parse_datetime(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except Exception:
        return None


def list_workflow_runs_files(workflow_runs_dir: str) -> list[Path]:
    p = Path(workflow_runs_dir)
    if not p.exists():
        return []
    return sorted([f for f in p.glob('*.json') if f.name.endswith('#runs.json')])


def load_runs_for_repo(runs_file: Path) -> dict[str, Any]:
    try:
        return json.loads(runs_file.read_text(encoding='utf-8'))
    except Exception as e:
        logger.error(f"Failed to load {runs_file}: {e}")
        return {}


def collect_failed_logs(failed_logs_root: str) -> tuple[dict[str, dict[str, RunRef]], set[str]]:
    """
    Index fail logs under failed_job_logs_fixed_params per repo/run.
    Returns ({owner#repo: {run_id: RunRef}}, repos_with_logs)
    """
    root = Path(failed_logs_root)
    result: dict[str, dict[str, RunRef]] = defaultdict(dict)
    repos_with_logs: set[str] = set()
    
    if not root.exists():
        logger.warning(f"Failed logs dir not found: {failed_logs_root}")
        return result, repos_with_logs
    
    # repos from subdir names
    for subdir in root.iterdir():
        if subdir.is_dir():
            # dir: owner#repo#workflow_id#run_id
            parts = subdir.name.split('#')
            if len(parts) >= 2:
                owner_repo = f"{parts[0]}#{parts[1]}"
                repos_with_logs.add(owner_repo)
    
    for file in root.glob('**/*#fail_log.txt'):
        parsed = parse_failed_log_filename(file.name)
        if not parsed:
            continue
        owner_repo, workflow_id, run_id, attempt, _ = parsed
        ref = result[owner_repo].get(run_id)
        if ref is None:
            result[owner_repo][run_id] = RunRef(
                run_id=run_id,
                workflow_id=workflow_id,
                created_at=None,
                updated_at=None,
                conclusion='failure',
                attempt=attempt,
                log_path=str(file.absolute()),
            )
        else:
            # keep highest attempt
            try:
                if ref.attempt is None or int(attempt) >= int(ref.attempt):
                    ref.attempt = attempt
                    ref.log_path = str(file.absolute())
            except Exception:
                ref.attempt = attempt
                ref.log_path = str(file.absolute())
    return result, repos_with_logs


def standardize_text(text: str) -> str:
    # light normalization
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    return t


def extract_error_snippet(log_path: str, tail_lines: int = 120) -> str:
    try:
        with open(log_path, encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        tail = ''.join(lines[-tail_lines:])
        # optional: trim from error marker
        # markers = ['=> ERROR', 'failed to', 'error:', 'FATA', 'ERROR:']
        # joined = ''.join(lines)
        # last_idx = -1
        # for m in markers:
        #     idx = joined.rfind(m)
        #     if idx > last_idx:
        #         last_idx = idx
        # if last_idx != -1:
        #     snippet = joined[last_idx:]
        #     return standardize_text(snippet)
        return standardize_text(tail)
    except Exception as e:
        logger.error(f"Read log failed {log_path}: {e}")
        return ''


def jaccard_similarity(a: str, b: str, ngram: int = 5) -> float:
    def shingles(s: str) -> set[str]:
        s = s.lower()
        if len(s) <= ngram:
            return {s}
        return {s[i:i+ngram] for i in range(0, len(s) - ngram + 1)}
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def cluster_by_threshold(items: list[tuple[str, str]], thresh: float = 0.8) -> list[list[str]]:
    """
    items: list of (run_id, snippet)
    Return clusters of run_ids
    """
    clusters: list[list[tuple[str, str]]] = []
    for run_id, txt in items:
        placed = False
        for c in clusters:
            # compare to cluster representative
            rep_id, rep_txt = c[0]
            if jaccard_similarity(txt, rep_txt) >= thresh:
                c.append((run_id, txt))
                placed = True
                break
        if not placed:
            clusters.append([(run_id, txt)])
    # flatten to run_ids
    return [[rid for rid, _ in c] for c in clusters]

def build_repo_failure_clusters(
    repo_key: str,
    runs_json: dict[str, Any],
    failed_logs_index: dict[str, RunRef],
    similarity_threshold: float = 0.8,
) -> dict[str, Any]:
    # failed runs with logs
    failed_runs_with_logs: list[RunRef] = []
    
    for workflow_id, wf in runs_json.items():
        for run in wf.get('runs', []):
            rid = str(run.get('id'))
            # only indexed failures
            if rid in failed_logs_index:
                ref = failed_logs_index[rid]
                rr = RunRef(
                    run_id=rid,
                    workflow_id=str(workflow_id),
                    created_at=run.get('created_at'),
                    updated_at=run.get('updated_at'),
                    conclusion='failure',  # treat as failure
                    attempt=ref.attempt,
                    log_path=ref.log_path,
                )
                failed_runs_with_logs.append(rr)

    if not failed_runs_with_logs:
        return {
            'repo': repo_key,
            'clusters': [],
        }

    # sort by run_id (newer is larger)
    failed_runs_with_logs.sort(key=lambda r: int(r.run_id))

    # error snippets
    items: list[tuple[str, str]] = []
    for rr in failed_runs_with_logs:
        snippet = extract_error_snippet(str(rr.log_path))
        items.append((rr.run_id, snippet))

    # cluster snippets
    clusters = cluster_by_threshold(items, similarity_threshold)

    # attach time span
    output_clusters = []
    for c_idx, c_run_ids in enumerate(clusters):
        # runs in cluster
        cluster_runs = [r for r in failed_runs_with_logs if r.run_id in c_run_ids]
        cluster_runs.sort(key=lambda r: int(r.run_id))
        
        output_clusters.append({
            'cluster_index': c_idx,
            'size': len(c_run_ids),
            'run_ids': c_run_ids,
            'run_ids_ordered': [r.run_id for r in cluster_runs],
            'start_time': cluster_runs[0].created_at if cluster_runs else None,
            'end_time': cluster_runs[-1].created_at if cluster_runs else None,
            'workflow_ids': list(set(r.workflow_id for r in cluster_runs if r.workflow_id)),
        })

    return {
        'repo': repo_key,
        'total_failed_runs': len(failed_runs_with_logs),
        'clusters': output_clusters,
    }


def run(
    workflow_runs_dir: str,
    failed_logs_dir: str,
    output_dir: str,
    similarity_threshold: float = 0.8,
):
    os.makedirs(output_dir, exist_ok=True)
    # index + repo filter
    failed_logs_index_by_repo, repos_with_logs = collect_failed_logs(failed_logs_dir)
    
    logger.info(f"Found {len(repos_with_logs)} repos with error logs")

    # only repos with logs
    processed_count = 0
    saved_count = 0
    for runs_file in list_workflow_runs_files(workflow_runs_dir):
        repo_key = parse_repo_key_from_filename(runs_file.name)
        
        # skip repos without logs
        if repo_key not in repos_with_logs:
            logger.debug(f"Skipping {repo_key} - no error logs found")
            continue
            
        processed_count += 1
        runs_json = load_runs_for_repo(runs_file)
        failed_index = failed_logs_index_by_repo.get(repo_key, {})
        result = build_repo_failure_clusters(repo_key, runs_json, failed_index, similarity_threshold)
        
        # skip empty clusters
        if not result.get('clusters'):
            logger.info(f"Skipping {repo_key} - no clustering results")
            continue
            
        out_path = Path(output_dir) / f"{repo_key}#clusters.json"
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        saved_count += 1
        logger.success(f"Saved clusters to {out_path}")
    
    logger.info(f"Processing complete: {processed_count} repos processed, {saved_count} files saved")


if __name__ == '__main__':
    from config import RESULTS_DIR
    workflow_runs_dir = str(RESULTS_DIR / "workflow_runs")
    failed_logs_dir = str(RESULTS_DIR / "failed_job_logs_fixed_params")
    output_dir = str(RESULTS_DIR / "failed_job_clusters")
    run(workflow_runs_dir, failed_logs_dir, output_dir, similarity_threshold=0.8)


