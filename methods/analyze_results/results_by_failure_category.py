# Run: cd project root && source .venv/bin/activate && python -m methods.analyze_results.results_by_failure_category
"""
Group by failure-log classification (failure_classification_dataset.json),
report repair success rates per method on the Cadre comparable set,
and output a LaTeX table (all taxonomy categories as columns).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import PROJECT_ROOT

from methods.analysis.classify_failures import TAXONOMY
from methods.analyze_results.comparable_results import get_unified_comparable_results
from methods.analyze_results.results_by_stage import (
    DEFAULT_COMPARE_TOOL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RESULT_PATHS,
    _tex_cell,
    _tex_method_header,
    methods_in_path_order,
)

DEFAULT_CLASSIFICATION = "results/failure_classification_dataset.json"
ALL_CATEGORIES = list(TAXONOMY.keys())


def load_category_map(classification_path: Path) -> dict[str, str]:
    with classification_path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {build_id: entry["category"] for build_id, entry in raw.items() if entry.get("category")}


def collect_by_category(
    compare_result: dict[str, dict],
    category_map: dict[str, str],
    *,
    compare_tool: str,
    categories: list[str],
    methods: list[str],
) -> dict:
    base = compare_result.get(compare_tool, {})
    comparable_ids = [
        bid
        for bid, status in base.items()
        if bid not in ("summary", "common_success") and status != "skipped"
    ]

    bucket_ids: dict[str, list[str]] = defaultdict(list)
    for bid in comparable_ids:
        cat = category_map.get(bid)
        if cat:
            bucket_ids[cat].append(bid)

    stats: dict[str, dict] = {}
    bucket_keys = categories + ["All"]
    for bucket in bucket_keys:
        ids = comparable_ids if bucket == "All" else bucket_ids.get(bucket, [])
        stats[bucket] = {"n": len(ids), "methods": {}}
        for method in methods:
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


def _category_header_label(category: str, n: int) -> str:
    if category == "All":
        return f"\\textbf{{All}} ($N$={n})"
    # LaTeX-safe category code
    code = category.replace("_", "\\_")
    if n == 0:
        return f"\\texttt{{{code}}}"
    return f"\\texttt{{{code}}}\\textsuperscript{{\\scriptsize {n}}}"


def latex_results_by_category_table(
    stats: dict,
    *,
    methods: list[str],
    categories: list[str],
    highlight: str = DEFAULT_COMPARE_TOOL,
    caption: str = "Repair rate on the comparable benchmark by failure-log category.",
    label: str = "tab:repair_by_failure_category",
) -> str:
    buckets = stats["buckets"]
    bucket_keys = categories + ["All"]
    cols = "l " + " ".join("c" for _ in bucket_keys)

    header = (
        "\\textbf{Method} & "
        + " & ".join(_category_header_label(c, buckets[c]["n"]) for c in bucket_keys)
        + " \\\\"
    )
    lines = [header, "\\midrule"]
    for method in methods:
        cells = [
            _tex_cell(
                buckets[bucket]["methods"][method]["success"],
                buckets[bucket]["methods"][method]["total"],
                buckets[bucket]["methods"][method]["rate"],
            )
            for bucket in bucket_keys
        ]
        lines.append(_tex_method_header(method, highlight) + " & " + " & ".join(cells) + " \\\\")

    body = "\n".join(lines)
    n_cols = len(bucket_keys) + 1
    return (
        "\\begin{table*}[t]\n"
        "\\centering\n"
        f"\\caption{{{caption}}}\n"
        f"\\label{{{label}}}\n"
        "\\resizebox{\\linewidth}{!}{%\n"
        "\\scriptsize\n"
        f"\\begin{{tabular}}{{{cols}}}\n"
        "\\toprule\n"
        f"{body}\n"
        "\\midrule\n"
        f"\\multicolumn{{{n_cols}}}{{l}}{{\\footnotesize "
        f"Comparable set: {stats['comparable_total']} builds where {highlight} "
        f"$\\neq$ \\texttt{{skipped}}; columns are failure categories from log classification. "
        f"Superscript on column headers: $N$ in comparable set.}} \\\\\n"
        "\\bottomrule\n"
        "\\end{tabular}%\n"
        "}\n"
        "\\end{table*}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair results by failure category (LaTeX)")
    parser.add_argument("--compare-tool", default=DEFAULT_COMPARE_TOOL)
    parser.add_argument("--classification", default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--methods", nargs="*", default=None)
    args = parser.parse_args()

    _, compare_result = get_unified_comparable_results(
        DEFAULT_RESULT_PATHS,
        compare_tool=args.compare_tool,
    )
    if args.compare_tool not in compare_result:
        loaded = ", ".join(compare_result.keys()) or "(none)"
        raise KeyError(
            f"compare_tool {args.compare_tool!r} not loaded. Loaded: {loaded}"
        )

    category_map = load_category_map(PROJECT_ROOT / args.classification)
    ordered_methods = methods_in_path_order(
        DEFAULT_RESULT_PATHS,
        compare_result,
        subset=args.methods,
    )
    if not ordered_methods:
        raise RuntimeError("No methods loaded.")

    stats = collect_by_category(
        compare_result,
        category_map,
        compare_tool=args.compare_tool,
        categories=ALL_CATEGORIES,
        methods=ordered_methods,
    )

    out_dir = PROJECT_ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    tex = latex_results_by_category_table(
        stats,
        methods=ordered_methods,
        categories=ALL_CATEGORIES,
        highlight=args.compare_tool,
    )
    tex_path = out_dir / "tab_results_by_failure_category.tex"
    json_path = out_dir / "results_by_failure_category.json"
    tex_path.write_text(tex, encoding="utf-8")
    json_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Comparable builds: {stats['comparable_total']}")
    print(f"Categories (taxonomy): {len(ALL_CATEGORIES)}")
    for cat in ALL_CATEGORIES:
        n = stats["buckets"][cat]["n"]
        if n:
            m = stats["buckets"][cat]["methods"][args.compare_tool]
            print(f"  {cat:16s} N={n:3d}  {args.compare_tool}: {m['success']}/{m['total']} ({m['rate']}%)")
    print(f"Wrote {tex_path.relative_to(PROJECT_ROOT)}")
    print(f"Wrote {json_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
