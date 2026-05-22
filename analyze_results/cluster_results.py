# Run with: python -m analyze_results.cluster_results
import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from config import FIXED_DOCKER_BUILDS_DIRNAME, PROJECT_ROOT, RESULTS_DIR

# Notes
# - Cluster files: {RESULTS_DIR}/failed_job_clusters/<repo>#<name>#clusters.json
#   Structure: { repo, clusters: [{ run_ids_ordered: ["<run_id>", ...], ...}, ...] }
#   We treat the position of a failed run in its cluster as its index in run_ids_ordered (0-based).
# - Tool run logs: .../fixed_docker_builds/<method>/<mode>/<model_slug>/run_logs/*.json
#   (or legacy .../fixed_docker_builds_<method>_<mode>_<slug>/run_logs)
#   Example full_build_tag: "ollama#ollama#<workflow_id>#<run_id>#<job_index>#<fix_method>"
# - We compute comparable runs based on a reference tool: take ALL its results excluding skipped,
#   then for all tools, evaluate on that same set; for those tools, treat skipped/missing as failures.
# - Comparable unit follows success_rate.py closer: include job_index (per-run attempt).
# - We aggregate success rates by cluster position buckets: 0,1,2,3, and 4+ (labeled "4+")

# Defaults: can be overridden via argparse in main(). Paths resolve to current workspace
# via config.RESULTS_DIR instead of legacy hardcoded absolute paths.
FAILED_JOB_CLUSTERS_DIR = str(RESULTS_DIR / "failed_job_clusters")
TOOL_RUN_LOG_ROOTS = [
    str(RESULTS_DIR / "ablation"),
    str(RESULTS_DIR),
]
OUTPUT_PDF = str(PROJECT_ROOT / "plots" / "cluster_success_by_position.pdf")
SELECTED_TOOLS = [
    'dofix_standard_DeepSeek-V3',
    'parfum',
    'dofix_standard_gpt-5',
    'pure-llm_standard_DeepSeek-V3',
    'flakidock_gpt_4_sv11',
    'flakidock_upgrade_response_deal_DeepSeek-V3',
]
LABEL_MAP = {
    'dofix_standard_DeepSeek-V3': 'DoFix-V3',
    'parfum': 'Parfum',
    'dofix_standard_gpt-5': 'DoFix-GPT-5',
    'pure-llm_standard_DeepSeek-V3': 'Pure-LLM-V3',
    'flakidock_gpt_4_sv11': 'FlakiDock-GPT4',
    'flakidock_upgrade_response_deal_DeepSeek-V3': 'FlakiDock-V3',
}
# Reference tool to define the comparable run set (must be in SELECTED_TOOLS)
COMPARE_TOOL = 'dofix_standard_DeepSeek-V3'


def _list_cluster_files() -> list[str]:
    files = []
    if not os.path.isdir(FAILED_JOB_CLUSTERS_DIR):
        return files
    for name in os.listdir(FAILED_JOB_CLUSTERS_DIR):
        if name.endswith("#clusters.json"):
            files.append(os.path.join(FAILED_JOB_CLUSTERS_DIR, name))
    return files


def _build_run_position_map() -> dict[tuple[str, str], int]:
    """Return mapping (repo_key, run_id) -> position_index_in_cluster (0-based)."""
    mapping: dict[tuple[str, str], int] = {}
    for fpath in _list_cluster_files():
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        repo_key = data.get("repo")  # format: owner#repo
        for cluster in data.get("clusters", []):
            run_ids_ordered = cluster.get("run_ids_ordered") or cluster.get("run_ids") or []
            for idx, run_id in enumerate(run_ids_ordered):
                mapping[(repo_key, str(run_id))] = idx
    return mapping


def _tool_name_from_fixed_docker_builds_run_logs(run_logs_dir: str) -> str | None:
    """Hierarchical layout: .../fixed_docker_builds/<method>/<mode>/<slug>/run_logs → {method}_{mode}_{slug};
    parfum has no mode/slug: .../fixed_docker_builds/parfum/run_logs → parfum; legacy flat fixed_docker_builds_* dirs still supported."""
    p = Path(run_logs_dir).resolve()
    if p.name != "run_logs":
        return None
    leaf = p.parent
    above = leaf.parent
    if above.name == FIXED_DOCKER_BUILDS_DIRNAME:
        return leaf.name
    mode_dir = leaf.parent
    method_dir = mode_dir.parent
    root_dir = method_dir.parent
    if root_dir.name == FIXED_DOCKER_BUILDS_DIRNAME:
        return f"{method_dir.name}_{mode_dir.name}_{leaf.name}"
    flat = leaf.name
    if flat.startswith("fixed_docker_builds_"):
        return flat[len("fixed_docker_builds_") :]
    return None


def _discover_tool_run_logs() -> dict[str, list[str]]:
    """Find tool run_log JSON files across known roots, deduplicated.

    Returns: tool_name -> list of file paths (each file is a repo's run log for that tool)
    tool_name: normally {method}_{mode}_{slug}; parfum is just parfum; legacy fixed_docker_builds_* strips the prefix.
    """
    tool_to_files_set: dict[str, set[str]] = defaultdict(set)
    visited_run_logs_dirs: set[str] = set()

    def try_add(root: str):
        if not os.path.isdir(root):
            return
        for dirpath, dirnames, _ in os.walk(root):
            base = os.path.basename(dirpath)
            if base == "run_logs":
                # Deduplicate same run_logs directory seen from multiple roots
                if dirpath in visited_run_logs_dirs:
                    dirnames[:] = []
                    continue
                visited_run_logs_dirs.add(dirpath)
                tool_name = _tool_name_from_fixed_docker_builds_run_logs(dirpath)
                if not tool_name:
                    dirnames[:] = []
                    continue
                if tool_name not in SELECTED_TOOLS:
                    print(f"Skipping {tool_name} because it is not in SELECTED_TOOLS")
                    dirnames[:] = []
                    continue
                for fname in os.listdir(dirpath):
                    if fname.endswith(".json"):
                        tool_to_files_set[tool_name].add(os.path.join(dirpath, fname))
                print(f"Added {len(tool_to_files_set[tool_name])} files for {tool_name}")
                # do not walk deeper from run_logs
                dirnames[:] = []

    for root in TOOL_RUN_LOG_ROOTS:
        try_add(root)

    # convert sets to lists
    return {tool: sorted(list(paths)) for tool, paths in tool_to_files_set.items()}


def _extract_repo_runid_job_from_full_build_tag(full_build_tag: str) -> tuple[str, str, str]:
    """Parse full_build_tag to (repo_key, run_id, job_index).

    Expected pattern: owner#repo#<workflow_id>#<run_id>#<job_index>#<fix_method>
    We return (owner#repo, run_id, job_index). If parsing fails, return ("", "", "").
    """
    try:
        parts = full_build_tag.split("#")
        if len(parts) < 6:
            return "", "", ""
        repo_key = f"{parts[0]}#{parts[1]}"
        run_id = parts[3]
        job_index = parts[4]
        return repo_key, run_id, job_index
    except Exception:
        return "", "", ""


def _collect_tool_status_by_run(tool_files: list[str]) -> dict[tuple[str, str, str], dict[str, int]]:
    """Collect per-attempt (run + job_index) status including skipped.

    Returns mapping (repo_key, run_id, job_index) -> {
      'has_any': 0/1, 'has_non_skipped': 0/1, 'has_success': 0/1
    }
    """
    per_attempt: dict[tuple[str, str, str], dict[str, int]] = {}
    status_set = set()
    for fpath in tool_files:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        for b in data.get("builds", []):
            status = b.get("status")
            full_build_tag = b.get("full_build_tag", "")
            repo_key, run_id, job_index = _extract_repo_runid_job_from_full_build_tag(full_build_tag)
            if not repo_key:
                continue
            key = (repo_key, run_id, job_index)
            if key not in per_attempt:
                per_attempt[key] = {"has_any": 0, "has_non_skipped": 0, "has_success": 0}
            per_attempt[key]["has_any"] = 1
            if status != "skipped" and status != 'rebuilding':
                per_attempt[key]["has_non_skipped"] = 1
                if status == "success":
                    per_attempt[key]["has_success"] = 1
            status_set.add(status)
    print(status_set)
    return per_attempt


def compute_success_rates_by_position() -> pd.DataFrame:
    """Compute success rates per tool and per cluster position bucket using reference-tool comparable set.

    Returns a DataFrame with columns: [tool, position_bucket, attempted, success, success_rate]
    position_bucket in {"1st", "2nd", "3rd", "4th", "5th+"}
    """
    run_position_map = _build_run_position_map()
    tool_to_files = _discover_tool_run_logs()

    if COMPARE_TOOL not in tool_to_files:
        print(f"Reference tool {COMPARE_TOOL} not found in discovered logs.")
        return pd.DataFrame()

    # 1) Build comparable set from reference tool: exclude skipped (i.e., only attempts with any non-skipped)
    ref_map = _collect_tool_status_by_run(tool_to_files[COMPARE_TOOL])
    comparable_attempts = {key for key, flags in ref_map.items() if flags.get("has_non_skipped", 0) == 1}
    print(f"Comparable attempts from reference tool: {len(comparable_attempts)}")

    if not comparable_attempts:
        print("No comparable attempts from reference tool.")
        return pd.DataFrame()

    # 2) Prepare per-tool flags over all attempts (include skipped).
    tool_flags: dict[str, dict[tuple[str, str, str], dict[str, int]]] = {}
    for tool, files in tool_to_files.items():
        if tool not in SELECTED_TOOLS:
            continue
        tool_flags[tool] = _collect_tool_status_by_run(files)

    # 3) Aggregate by bucket over comparable attempts
    def bucket_of(pos: int) -> str:
        if pos == 0:
            return "1st"
        if pos == 1:
            return "2nd"
        if pos == 2:
            return "3rd"
        if pos == 3:
            return "4th"
        return "5th+"

    counts: list[dict[str, object]] = []
    buckets = ["1st", "2nd", "3rd", "4th", "5th+"]

    # Map attempts to buckets using (repo_key, run_id) for position lookup
    bucket_to_attempts: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for (repo_key, run_id, job_index) in comparable_attempts:
        pos = run_position_map.get((repo_key, run_id))
        if pos is None:
            continue
        bucket_to_attempts[bucket_of(pos)].append((repo_key, run_id, job_index))

    for tool in SELECTED_TOOLS:
        flags = tool_flags.get(tool, {})
        for bucket in buckets:
            attempts = bucket_to_attempts.get(bucket, [])
            attempted = len(attempts)  # denominator fixed by reference set at attempt level
            success = 0
            for key in attempts:
                f = flags.get(key)
                if f and f.get("has_success", 0) == 1:
                    success += 1
                else:
                    # skipped-only or missing or non-skipped failures → count as failure
                    pass
            success_rate = (success / attempted) if attempted > 0 else 0.0
            counts.append({
                "tool": tool,
                "position_bucket": bucket,
                "attempted": attempted,
                "success": success,
                "success_rate": success_rate,
            })

    df = pd.DataFrame.from_records(counts)
    if not df.empty:
        df["position_bucket"] = pd.Categorical(df["position_bucket"], categories=buckets, ordered=True)
    return df


def plot_grouped_bar(df: pd.DataFrame, output_pdf: str = OUTPUT_PDF):
    if df.empty:
        print("No data to plot.")
        return
    plt.figure(figsize=(14, 6))
    sns.set_palette("tab10")

    # Pivot for grouped bars: index=bucket, columns=tool, values=success_rate
    pivot = df.pivot_table(index="position_bucket", columns="tool", values="success_rate", aggfunc="mean")
    pivot = pivot.sort_index()
    # Matching attempted pivot for x-axis labels
    attempted_pivot = df.pivot_table(index="position_bucket", columns="tool", values="attempted", aggfunc="sum")
    attempted_pivot = attempted_pivot.reindex(index=pivot.index, columns=pivot.columns)
    attempts_per_bucket = attempted_pivot.max(axis=1)

    # Display labels
    pivot.rename(columns=LABEL_MAP, inplace=True)

    # Plot
    ax = pivot.plot(kind="bar", figsize=(14, 6))
    # Build x tick labels as "bucket (n=xxx)"
    xticklabels = []
    for bucket in pivot.index:
        n_val = int(attempts_per_bucket.loc[bucket]) if bucket in attempts_per_bucket.index else 0
        xticklabels.append(f"{bucket} (n={n_val})")
    ax.set_xticklabels(xticklabels, rotation=0)

    ax.set_xlabel("Cluster position of failed run")
    ax.set_ylabel("Repair success rate")
    ax.set_title("Per-tool repair success rate by failed-run position in cluster")
    ax.legend(title="Tool", bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.grid(axis='y', alpha=0.3)

    # Annotate each bar with percentage only
    for p in ax.patches:
        height = p.get_height()
        if pd.isna(height):
            continue
        ax.annotate(f"{height:.1%}", (p.get_x() + p.get_width() / 2., height),
                    ha='center', va='bottom', fontsize=8, xytext=(0, 3), textcoords='offset points')

    plt.tight_layout()
    plt.savefig(output_pdf, dpi=600, bbox_inches='tight')
    print(f"Saved plot to {output_pdf}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot per-tool repair success rates bucketed by cluster position.")
    parser.add_argument("--clusters_dir", default=FAILED_JOB_CLUSTERS_DIR, help="failed_job_clusters directory (contains *#clusters.json).")
    parser.add_argument("--tool_run_log_roots", nargs="+", default=TOOL_RUN_LOG_ROOTS, help="Search roots containing fixed_docker_builds/.../run_logs; may specify multiple.")
    parser.add_argument("--selected_tools", nargs="+", default=SELECTED_TOOLS, help="Tool id: method_mode_slug, parfum, or legacy fixed_docker_builds_* suffix.")
    parser.add_argument("--compare_tool", default=COMPARE_TOOL, help="Reference tool id; must appear in --selected_tools.")
    parser.add_argument("--output", default=OUTPUT_PDF, help="Output PDF path.")
    return parser.parse_args()


def main():
    args = _parse_args()
    # Override module-level defaults so existing helpers keep working without signature churn.
    global FAILED_JOB_CLUSTERS_DIR, TOOL_RUN_LOG_ROOTS, SELECTED_TOOLS, COMPARE_TOOL, OUTPUT_PDF
    FAILED_JOB_CLUSTERS_DIR = args.clusters_dir
    TOOL_RUN_LOG_ROOTS = list(args.tool_run_log_roots)
    SELECTED_TOOLS = list(args.selected_tools)
    COMPARE_TOOL = args.compare_tool
    OUTPUT_PDF = args.output
    os.makedirs(os.path.dirname(OUTPUT_PDF) or ".", exist_ok=True)

    df = compute_success_rates_by_position()
    if df.empty:
        print("No data computed. Check inputs.")
        return
    summary = df.sort_values(["position_bucket", "tool"])
    print(summary.to_string(index=False))
    plot_grouped_bar(df, output_pdf=OUTPUT_PDF)


if __name__ == "__main__":
    main()
