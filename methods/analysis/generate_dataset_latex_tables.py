# Run: source .venv/bin/activate && python -m methods.analysis.generate_dataset_latex_tables --help
"""
Generate LaTeX tables and narrative text for D^3 dataset statistics.

Reads:  results/dataset_valid_fixed_params_with_dockerfile_relative.json

Writes: results/dataset_latex/
          tab_dataset_statistics.tex  — single unified table (default)
          dataset_stats.json
          dataset_narrative.txt
          dataset_tables.tex          — \\input{tab_dataset_statistics}
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from dockerfile_parse import DockerfileParser

# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def _normalize_platform(platform: Any) -> str:
    if platform is None:
        return "unspecified"
    if isinstance(platform, str):
        return platform.strip() or "unspecified"
    if isinstance(platform, list):
        parts = sorted(str(p).strip() for p in platform if p)
        if not parts:
            return "unspecified"
        if len(parts) == 1:
            return parts[0]
        return " + ".join(parts)
    return str(platform)


def _first_from_image(value: str) -> str:
    """Crude token for base-image diversity (first image ref in FROM value)."""
    parts = value.split()
    for p in parts:
        if p.upper() == "AS":
            break
        if not p.startswith("--") and p not in ("AS", "as"):
            return p[:80]
    return value[:80] if value else "unknown"


def collect_entry_metrics(entry: dict) -> dict[str, Any] | None:
    content = entry.get("dockerfile_content") or ""
    if not content.strip():
        return None

    bp = entry.get("build_params") or {}
    diff_info = entry.get("diff_info") or {}
    error_log = entry.get("error_log") or ""

    try:
        dfp = DockerfileParser(fileobj=io.BytesIO(content.encode()))
        structure = dfp.structure
    except Exception as e:
        return {"parse_error": str(e)}

    instructions = [x for x in structure if x.get("instruction") not in ("COMMENT",)]
    froms = [x for x in structure if x.get("instruction") == "FROM"]
    base_images = [_first_from_image(x.get("value", "")) for x in froms]

    return {
        "stage_count": len(froms),
        "instruction_count": len(instructions),
        "run_count": sum(1 for x in structure if x.get("instruction") == "RUN"),
        "copy_add_count": sum(
            1 for x in structure if x.get("instruction") in ("COPY", "ADD")
        ),
        "base_images": base_images,
        "diff_file_count": len(diff_info),
        "platform": _normalize_platform(bp.get("platform")),
        "error_log_chars": len(error_log),
    }


def collect_dataset(dataset_path: Path) -> tuple[list[dict], dict]:
    with dataset_path.open(encoding="utf-8") as f:
        data = json.load(f)

    rows: list[dict] = []
    skipped_empty = 0
    parse_errors = 0

    for repo, jobs in data.items():
        for build_id, entry in jobs.items():
            m = collect_entry_metrics(entry)
            if m is None:
                skipped_empty += 1
                continue
            if m.get("parse_error"):
                parse_errors += 1
                continue
            m["repo"] = repo
            m["build_id"] = build_id
            rows.append(m)

    n = len(rows)
    stage_counts = [r["stage_count"] for r in rows]
    platforms = Counter(r["platform"] for r in rows)
    all_base_images = {img for r in rows for img in r["base_images"]}

    def dist_counter(values: list[int], collapse_from: int | None = None) -> dict[str, int]:
        c = Counter(values)
        if collapse_from is None:
            return {str(k): c[k] for k in sorted(c)}
        out: dict[str, int] = {}
        tail = 0
        for k in sorted(c):
            if k < collapse_from:
                out[str(k)] = c[k]
            else:
                tail += c[k]
        if tail:
            out[f"{collapse_from}+"] = tail
        return out

    stats: dict[str, Any] = {
        "dataset": str(dataset_path),
        "total_entries_in_json": sum(len(j) for j in data.values()),
        "parsed_entries": n,
        "skipped_empty_dockerfile": skipped_empty,
        "parse_errors": parse_errors,
        "unique_repositories": len(data),
        "unique_base_images": len(all_base_images),
        "stage_distribution": dist_counter(stage_counts, collapse_from=4),
        "platform_distribution": dict(platforms.most_common()),
        "continuous": {
            "instruction_count": _continuous_summary([r["instruction_count"] for r in rows]),
            "run_count": _continuous_summary([r["run_count"] for r in rows]),
            "copy_add_count": _continuous_summary([r["copy_add_count"] for r in rows]),
            "diff_file_count": _continuous_summary([r["diff_file_count"] for r in rows]),
            "error_log_chars": _continuous_summary([r["error_log_chars"] for r in rows]),
        },
        "diff_file_bins": _bin_counts([r["diff_file_count"] for r in rows], DIFF_BINS),
        "error_log_bins": _bin_counts([r["error_log_chars"] for r in rows], LOG_BINS),
        "multi_stage_pct": round(100 * sum(1 for s in stage_counts if s >= 2) / n, 2) if n else 0,
    }
    return rows, stats


def _continuous_summary(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {}
    s = sorted(values)
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(statistics.mean(values), 2),
        "median": statistics.median(values),
        "stdev": round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
        "p90": s[int(0.9 * (len(s) - 1))],
    }


DIFF_BINS = [(0, 0), (1, 1), (2, 5), (6, 10), (11, 20), (21, 50), (51, 10**9)]
LOG_BINS = [(0, 500), (501, 1000), (1001, 2000), (2001, 5000), (5001, 10000), (10001, 10**9)]


def _collapse_top_n(distribution: dict[str, int], top_n: int = 5) -> dict[str, int]:
    items = sorted(distribution.items(), key=lambda x: -x[1])
    if len(items) <= top_n:
        return dict(items)
    out = dict(items[:top_n])
    other = sum(c for _, c in items[top_n:])
    if other:
        out["Other"] = other
    return out


def _tex_escape(s: str) -> str:
    return (
        s.replace("\\", "\\textbackslash{}")
        .replace("_", "\\_")
        .replace("%", "\\%")
        .replace("&", "\\&")
    )


def _bin_counts(values: list[int], bins: list[tuple[int, int]]) -> dict[str, int]:
    labels = []
    for lo, hi in bins:
        if lo == hi:
            labels.append(str(lo))
        else:
            labels.append(f"{lo}--{hi}" if hi < 10**8 else f"{lo}+")
    counts = [0] * len(bins)
    for v in values:
        for i, (lo, hi) in enumerate(bins):
            if lo <= v <= hi:
                counts[i] += 1
                break
    return {labels[i]: counts[i] for i in range(len(bins)) if counts[i] > 0}


# ---------------------------------------------------------------------------
# LaTeX helpers
# ---------------------------------------------------------------------------

def _pct(count: int, total: int) -> str:
    if total == 0:
        return "0.0"
    return f"{100.0 * count / total:.1f}"


def _fmt_num(x: float | int) -> str:
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float) and x == int(x):
        return str(int(x))
    if isinstance(x, float):
        return f"{x:.2f}".rstrip("0").rstrip(".")
    return str(x)


def latex_table_wrapper(
    body: str,
    caption: str,
    label: str,
    *,
    col_spec: str = "@{}lrr@{}",
    table_star: bool = False,
    resize_column: bool = False,
    resize_linewidth: bool = False,
) -> str:
    env = "table*" if table_star else "table"
    if resize_linewidth:
        col_note = "\\resizebox{\\linewidth}{!}{%\n"
        col_end = "%\n}\n"
    elif resize_column and not table_star:
        col_note = "\\resizebox{\\columnwidth}{!}{%\n"
        col_end = "%\n}\n"
    else:
        col_note = ""
        col_end = ""
    return (
        f"\\begin{{{env}}}[t]\n"
        f"\\centering\n"
        f"\\caption{{{caption}}}\n"
        f"\\label{{{label}}}\n"
        f"{col_note}"
        f"\\begin{{tabular}}{{{col_spec}}}\n"
        f"\\toprule\n"
        f"{body}\n"
        f"\\bottomrule\n"
        f"\\end{{tabular}}\n"
        f"{col_end}"
        f"\\end{{{env}}}\n"
    )


def latex_discrete_distribution(
    distribution: dict[str, int],
    total: int,
    row_header: str,
    caption: str,
    label: str,
    **kwargs: Any,
) -> str:
    lines = [
        f"\\textbf{{{row_header}}} & \\textbf{{Count}} & \\textbf{{\\%}} \\\\",
        "\\midrule",
    ]
    for key, count in distribution.items():
        lines.append(f"{key} & {count} & {_pct(count, total)}\\% \\\\")
    body = "\n".join(lines)
    return latex_table_wrapper(body, caption, label, **kwargs)


def latex_continuous_summary_table(continuous: dict[str, dict], caption: str, label: str) -> str:
    lines = [
        "\\textbf{Metric} & \\textbf{Min} & \\textbf{Med.} & \\textbf{Mean} & \\textbf{Max} & \\textbf{P90} \\\\",
        "\\midrule",
    ]
    labels = {
        "instruction_count": "Dockerfile instructions",
        "run_count": "\\texttt{RUN} instructions",
        "copy_add_count": "\\texttt{COPY}+\\texttt{ADD}",
        "diff_file_count": "Changed files per build",
        "error_log_chars": "Error log length (chars)",
    }
    for key, title in labels.items():
        s = continuous.get(key, {})
        if not s:
            continue
        lines.append(
            f"{title} & {_fmt_num(s['min'])} & {_fmt_num(s['median'])} & {_fmt_num(s['mean'])} "
            f"& {_fmt_num(s['max'])} & {_fmt_num(s['p90'])} \\\\"
        )
    return latex_table_wrapper(
        "\n".join(lines),
        caption,
        label,
        col_spec="@{}lrrrrr@{}",
        resize_column=True,
    )


def latex_binned_table(
    bins: dict[str, int],
    total: int,
    row_header: str,
    caption: str,
    label: str,
) -> str:
    return latex_discrete_distribution(bins, total, row_header, caption, label, resize_column=True)


def latex_unified_dataset_table(stats: dict, *, platform_top_n: int = 5) -> str:
    """One table: scale, stages, platforms, complexity, co-change & log distributions."""
    n = stats["parsed_entries"]
    c = stats["continuous"]
    multi_stage_n = sum(
        int(v) for k, v in stats["stage_distribution"].items() if k != "1"
    )

    lines: list[str] = [
        "\\textbf{Metric} & \\textbf{Count / Value} & \\textbf{\\%} \\\\",
        "\\midrule",
        "\\multicolumn{3}{l}{\\textit{Dataset scale}} \\\\",
        f"Repositories & {stats['unique_repositories']} & -- \\\\",
        f"Build instances & {n} & 100.0\\\\",
        "\\midrule",
        "\\multicolumn{3}{l}{\\textit{Build stages (\\#\\texttt{FROM})}} \\\\",
    ]

    for key, count in stats["stage_distribution"].items():
        label = f"{key} stage(s)" if not key.endswith("+") else f"{key} stages"
        lines.append(f"{label} & {count} & {_pct(count, n)}\\\\")

    lines.append("\\midrule")
    lines.append("\\multicolumn{3}{l}{\\textit{Target platform(s) from CI build parameters}} \\\\")

    platform_dist = _collapse_top_n(stats["platform_distribution"], platform_top_n)
    for plat, count in platform_dist.items():
        plat_tex = f"\\texttt{{{_tex_escape(plat)}}}" if plat != "Other" else "Other"
        lines.append(f"{plat_tex} & {count} & {_pct(count, n)}\\\\")

    lines.append("\\midrule")
    lines.append(
        "\\multicolumn{3}{l}{\\textit{Dockerfile structure (median / mean; min--max)}} \\\\"
    )

    struct_rows = [
        ("Non-comment instructions", "instruction_count"),
        ("\\texttt{RUN} instructions", "run_count"),
        ("\\texttt{COPY}+\\texttt{ADD} instructions", "copy_add_count"),
    ]
    for title, key in struct_rows:
        s = c[key]
        val = (
            f"{_fmt_num(s['median'])} / {_fmt_num(s['mean'])} "
            f"({_fmt_num(s['min'])}--{_fmt_num(s['max'])})"
        )
        lines.append(f"{title} & {val} & -- \\\\")

    lines.append(f"Distinct base image references & {stats['unique_base_images']} & -- \\\\")
    lines.append(
        f"Multi-stage builds ($\\geq$2 \\texttt{{FROM}}) & {multi_stage_n} & "
        f"{stats['multi_stage_pct']}\\\\"
    )

    lines.append("\\midrule")
    lines.append("\\multicolumn{3}{l}{\\textit{Co-change: changed files per build (bin)}} \\\\")
    for label, count in stats["diff_file_bins"].items():
        lines.append(f"{label} file(s) & {count} & {_pct(count, n)}\\\\")

    lines.append("\\midrule")
    lines.append("\\multicolumn{3}{l}{\\textit{Failure log length (characters, bin)}} \\\\")
    for label, count in stats["error_log_bins"].items():
        lines.append(f"{label} chars & {count} & {_pct(count, n)}\\\\")

    body = "\n".join(lines)
    return latex_table_wrapper(
        body,
        "Statistical overview of the $D^3$ dataset.",
        "tab:d3_dataset_statistics",
        col_spec="@{}lrl@{}",
        table_star=True,
        resize_linewidth=True,
    )


# ---------------------------------------------------------------------------
# Narrative (for prose, not tables)
# ---------------------------------------------------------------------------

def generate_narrative(stats: dict) -> str:
    n = stats["parsed_entries"]
    c = stats["continuous"]
    lines = [
        "=== Suggested: present in prose in the main text (copy and adapt for the paper) ===",
        "",
        f"Scale. The $D^3$ benchmark comprises {stats['unique_repositories']} GitHub "
        f"repositories and {n} failed Docker build instances with full build context.",
        "",
        "Base images. Across all FROM instructions, we observe "
        f"{stats['unique_base_images']} distinct base image references, indicating "
        "substantial diversity in container starting points.",
        "",
        "Continuous complexity (median / mean). "
        f"Dockerfiles contain a median of {c['instruction_count']['median']:.0f} "
        f"(mean {c['instruction_count']['mean']}) non-comment instructions, "
        f"with {c['run_count']['median']:.0f} ({c['run_count']['mean']}) RUN and "
        f"{c['copy_add_count']['median']:.0f} ({c['copy_add_count']['mean']}) COPY/ADD steps. "
        f"Each instance is associated with a median of {c['diff_file_count']['median']:.0f} "
        f"(mean {c['diff_file_count']['mean']}) changed source files at the failing commit, "
        f"and failure logs of median length {c['error_log_chars']['median']:.0f} characters "
        f"(mean {c['error_log_chars']['mean']:.0f}).",
        "",
        f"Multi-stage builds account for {stats['multi_stage_pct']}% of instances "
        f"(builds with $\\geq$2 FROM instructions).",
        "",
        "=== LaTeX ===",
        "",
        "Use a single table: \\input{results/dataset_latex/tab_dataset_statistics}",
        "",
    ]
    return "\n".join(lines)


def generate_placement_readme() -> str:
    return """# Dataset statistics (single table)

All metrics are merged into `tab_dataset_statistics.tex`.

```latex
\\input{results/dataset_latex/tab_dataset_statistics}
```

Requires in preamble: `booktabs`, `graphicx` (for \\resizebox).

Platform rows show top-5 combinations plus ``Other''. Re-run with
`--platform-top-n` to adjust.
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def write_outputs(stats: dict, out_dir: Path, *, platform_top_n: int = 5) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    unified = latex_unified_dataset_table(stats, platform_top_n=platform_top_n)
    (out_dir / "tab_dataset_statistics.tex").write_text(unified, encoding="utf-8")

    wrapper = (
        "% Auto-generated by methods/analysis/generate_dataset_latex_tables.py\n"
        f"\\input{{{out_dir.as_posix()}/tab_dataset_statistics}}\n"
    )
    (out_dir / "dataset_tables.tex").write_text(wrapper, encoding="utf-8")

    for obsolete in (
        "tab_stages.tex",
        "tab_platforms.tex",
        "tab_continuous_summary.tex",
        "tab_diff_files_bins.tex",
        "tab_error_log_bins.tex",
    ):
        p = out_dir / obsolete
        if p.exists():
            p.unlink()

    (out_dir / "dataset_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "dataset_narrative.txt").write_text(generate_narrative(stats), encoding="utf-8")
    (out_dir / "README_placement.md").write_text(generate_placement_readme(), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate LaTeX tables for D^3 dataset statistics")
    parser.add_argument(
        "--dataset",
        default="results/dataset_valid_fixed_params_with_dockerfile_relative.json",
    )
    parser.add_argument(
        "--output-dir",
        default="results/dataset_latex",
        help="Directory for .tex fragments and JSON",
    )
    parser.add_argument(
        "--platform-top-n",
        type=int,
        default=5,
        help="Top-N platform combinations in the unified table; rest merged as Other",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    out_dir = Path(args.output_dir)

    _, stats = collect_dataset(dataset_path)
    write_outputs(stats, out_dir, platform_top_n=args.platform_top_n)

    print(f"Parsed {stats['parsed_entries']} / {stats['total_entries_in_json']} entries")
    print(f"Unique base images: {stats['unique_base_images']}")
    print(f"Wrote -> {out_dir}/tab_dataset_statistics.tex")
    print(f"       {out_dir}/dataset_stats.json, dataset_narrative.txt")


if __name__ == "__main__":
    main()
