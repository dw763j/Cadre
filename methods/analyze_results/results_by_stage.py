# Run: cd project root && source .venv/bin/activate && python -m methods.analyze_results.results_by_stage
"""
Group by Dockerfile build stage count and report repair success rates per method
on the Cadre comparable set, outputting a LaTeX table.

The comparable set matches comparable_results.py: builds where Cadre run_logs
have status != skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import PROJECT_ROOT

from methods.analyze_results.comparable_results import get_unified_comparable_results

DEFAULT_COMPARE_TOOL = "Cadre"
DEFAULT_STAGE_COUNTS = "results/dockerfile_stage_counts.json"
DEFAULT_OUTPUT_DIR = "results/dataset_latex"

DEFAULT_RESULT_PATHS: dict[str, str] = {
    "Parfum": "results/ablation/fixed_docker_builds_parfum/run_logs",
    "FlakiDock": "results/ablation/fixed_docker_builds_flakidock_gpt_4_sv11/run_logs",
    "FlakiDock-DS-V3": "results/ablation/fixed_docker_builds_flakidock_upgrade_response_deal_DeepSeek-V3/run_logs",
    # "flakidock_ori_v3": "results/ablation/fixed_docker_builds_flakidock_DeepSeek-V3/run_logs",
    "Vanilla-LLM": "results/ablation/fixed_docker_builds_pure_llm_DeepSeek-V3/run_logs",
    "Cadre": "results/fixed_docker_builds/dofix/standard/DeepSeek-V3.2509/run_logs",
    # "Cadre-R1": "results/fixed_docker_builds/dofix/standard/DeepSeek-R1/run_logs",
    # "Cadre-Qwen3": "results/fixed_docker_builds/dofix/standard/Qwen3-235B-A22B-Instruct-2507/run_logs",
    # "DoFix-V3-Remove-Key-Files": "results/fixed_docker_builds/dofix/remove_key_files/DeepSeek-V3/run_logs",
    # "DoFix-V3-Remove-Build-Channel": (
    #     "results/ablation/fixed_docker_builds_dofix_remove_build_channel_DeepSeek-V3/run_logs"
    # ),
}

STAGE_BUCKETS = ["1", "2", "3", "4+"]


def methods_in_path_order(
    result_paths: dict[str, str],
    compare_result: dict[str, dict],
    *,
    subset: list[str] | None = None,
) -> list[str]:
    """Return successfully loaded method names in result_paths declaration order."""
    names = subset if subset is not None else list(result_paths.keys())
    return [m for m in names if m in compare_result]


def stage_bucket(stage_count: int) -> str:
    return str(stage_count) if stage_count < 4 else "4+"


def load_stage_map(stage_counts_path: Path) -> dict[str, int]:
    with stage_counts_path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {
        build_id: int(entry["stage_count"])
        for build_id, entry in raw.items()
        if entry.get("stage_count") is not None
    }


def collect_by_stage(
    compare_result: dict[str, dict],
    stage_map: dict[str, int],
    *,
    compare_tool: str,
    methods: list[str] | None = None,
) -> dict:
    """Return success/total aggregated by stage bucket and method."""
    base = compare_result.get(compare_tool, {})
    comparable_ids = [
        bid
        for bid, status in base.items()
        if bid not in ("summary", "common_success") and status != "skipped"
    ]

    bucket_ids: dict[str, list[str]] = defaultdict(list)
    for bid in comparable_ids:
        if bid not in stage_map:
            continue
        bucket_ids[stage_bucket(stage_map[bid])].append(bid)

    method_names = methods or list(compare_result.keys())

    stats: dict[str, dict[str, dict[str, int | float]]] = {}
    for bucket in STAGE_BUCKETS + ["All"]:
        ids = comparable_ids if bucket == "All" else bucket_ids.get(bucket, [])
        stats[bucket] = {"n": len(ids), "methods": {}}
        for method in method_names:
            method_result = compare_result.get(method, {})
            success = sum(1 for bid in ids if method_result.get(bid) == "success")
            total = len(ids)
            stats[bucket]["methods"][method] = {
                "success": success,
                "total": total,
                "rate": round(100 * success / total, 2) if total else 0.0,
            }

    return {
        "compare_tool": compare_tool,
        "comparable_total": len(comparable_ids),
        "buckets": stats,
    }


def _tex_method_header(name: str, highlight: str) -> str:
    if name == highlight:
        return f"\\textbf{{{name}}}"
    return name


def _tex_cell(success: int, total: int, rate: float) -> str:
    if total == 0:
        return "--"
    return f"{success}/{total} ({rate:.1f}\\%)"


def _bucket_header_label(bucket: str, n: int) -> str:
    if bucket == "All":
        return f"\\textbf{{All}} ($N$={n})"
    label = bucket if bucket != "4+" else "4+"
    return f"\\textbf{{{label}}} ($N$={n})"


def latex_results_by_stage_table(
    stats: dict,
    *,
    methods: list[str],
    highlight: str = DEFAULT_COMPARE_TOOL,
    caption: str = "Repair rate distribution by Dockerfile build-stage count.",
    label: str = "tab:repair_by_stage",
) -> str:
    buckets = stats["buckets"]
    bucket_keys = STAGE_BUCKETS + ["All"]
    n_cols = 1 + len(bucket_keys)
    cols = "l " + " ".join("c" for _ in bucket_keys)

    header = (
        "\\textbf{Method} & "
        + " & ".join(_bucket_header_label(b, buckets[b]["n"]) for b in bucket_keys)
        + " \\\\"
    )
    lines = [header, "\\midrule"]
    for method in methods:
        cells = []
        for bucket in bucket_keys:
            m = buckets[bucket]["methods"][method]
            cells.append(_tex_cell(m["success"], m["total"], m["rate"]))
        lines.append(
            _tex_method_header(method, highlight) + " & " + " & ".join(cells) + " \\\\"
        )

    body = "\n".join(lines)
    return (
        "\\begin{table*}[t]\n"
        "\\centering\n"
        f"\\caption{{{caption}}}\n"
        f"\\label{{{label}}}\n"
        "\\resizebox{\\linewidth}{!}{%\n"
        f"\\begin{{tabular}}{{{cols}}}\n"
        "\\toprule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}%\n"
        "}\n"
        "\\end{table*}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair results by Dockerfile stage count (LaTeX)")
    parser.add_argument("--compare-tool", default=DEFAULT_COMPARE_TOOL)
    parser.add_argument("--stage-counts", default=DEFAULT_STAGE_COUNTS)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--methods",
        nargs="*",
        default=None,
        help="Subset of methods (default: all loaded from result paths)",
    )
    args = parser.parse_args()

    _, compare_result = get_unified_comparable_results(
        DEFAULT_RESULT_PATHS,
        compare_tool=args.compare_tool,
    )
    if args.compare_tool not in compare_result:
        loaded = ", ".join(compare_result.keys()) or "(none)"
        raise KeyError(
            f"compare_tool {args.compare_tool!r} not loaded. "
            f"Check run_logs path in DEFAULT_RESULT_PATHS. Loaded: {loaded}"
        )

    stage_map = load_stage_map(PROJECT_ROOT / args.stage_counts)
    ordered_methods = methods_in_path_order(
        DEFAULT_RESULT_PATHS,
        compare_result,
        subset=args.methods,
    )
    if not ordered_methods:
        raise RuntimeError("No methods loaded; check DEFAULT_RESULT_PATHS and run_logs directories.")

    stats = collect_by_stage(
        compare_result,
        stage_map,
        compare_tool=args.compare_tool,
        methods=ordered_methods,
    )

    out_dir = PROJECT_ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    tex = latex_results_by_stage_table(stats, methods=ordered_methods, highlight=args.compare_tool)
    tex_path = out_dir / "tab_results_by_stage.tex"
    json_path = out_dir / "results_by_stage.json"
    tex_path.write_text(tex, encoding="utf-8")
    json_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Comparable builds: {stats['comparable_total']}")
    for bucket in STAGE_BUCKETS + ["All"]:
        n = stats["buckets"][bucket]["n"]
        dofix = stats["buckets"][bucket]["methods"].get(args.compare_tool, {})
        print(f"  stages={bucket:3s} N={n:3d}  {args.compare_tool}: {dofix.get('success', 0)}/{dofix.get('total', 0)} ({dofix.get('rate', 0)}%)")
    print(f"Wrote {tex_path.relative_to(PROJECT_ROOT)}")
    print(f"Wrote {json_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
