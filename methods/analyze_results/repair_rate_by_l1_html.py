# Run: cd project root && source .venv/bin/activate && python -m methods.analyze_results.repair_rate_by_l1_html
"""
Compare repair success rates across dofix/standard models by L1 category from classify.md; output HTML.

Comparable set: builds where DeepSeek-V3.2509 run_logs have status != skipped (653 builds).
Each model reports success on the same build subset (same as comparable_results).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import PROJECT_ROOT

DEFAULT_CLASSIFICATION = "results/failure_classification_dataset.json"
DEFAULT_OUTPUT = "results/analysis/repair_rate_by_l1_model_compare.html"

# L2/L3 code -> L1 (matches methods/analysis/classify.md)
CODE_TO_L1: dict[str, str] = {
    "APP_UV": "Application Dependency & Build",
    "APP_PIP": "Application Dependency & Build",
    "APP_PYPACKAGE": "Application Dependency & Build",
    "APP_NPM": "Application Dependency & Build",
    "APP_PNPM": "Application Dependency & Build",
    "APP_GO": "Application Dependency & Build",
    "APP_OTHER": "Application Dependency & Build",
    "APP_MAKE": "Application Dependency & Build",
    "SYS_MISSING": "System & Environment",
    "SYS_PKG": "System & Environment",
    "SYS_PYVER": "System & Environment",
    "SYS_DOWNLOAD": "System & Environment",
    "DOCKER_IMAGE": "Dockerfile-specific",
    "DOCKER_COPY": "Dockerfile-specific",
    "SCRIPT_CUSTOM": "Script & Command",
    "SCRIPT_SYNTAX": "Script & Command",
    "INFRA_CACHE": "CI Infrastructure",
    "INFRA_DISK": "CI Infrastructure",
    "INFRA_PUSH": "CI Infrastructure",
    "UNFIXABLE": "Software Internal",
    "UNKNOWN": "Software Internal",
}

L1_ORDER = [
    "Application Dependency & Build",
    "System & Environment",
    "Dockerfile-specific",
    "Script & Command",
    "CI Infrastructure",
    "Software Internal",
]

DEFAULT_COMPARE_BASELINE = "DeepSeek-V3.2509"

DEFAULT_MODEL_RUN_LOGS: dict[str, str] = {
    "DeepSeek-R1": "results/fixed_docker_builds/dofix/standard/DeepSeek-R1/run_logs",
    "DeepSeek-V3": "results/fixed_docker_builds/dofix/standard/DeepSeek-V3/run_logs",
    "DeepSeek-V3.2509": "results/fixed_docker_builds/dofix/standard/DeepSeek-V3.2509/run_logs",
    "Qwen3-235B": "results/fixed_docker_builds/dofix/standard/Qwen3-235B-A22B-Instruct-2507/run_logs",
}


def load_build_status(run_logs_dir: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for path in run_logs_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for build in data.get("builds", []):
            tag = build.get("full_build_tag", "")
            fix_method = build.get("fix_method", "")
            key = tag.replace(f"#{fix_method}", "") if fix_method else tag
            statuses[key] = build.get("status", "unknown")
    return statuses


def load_l1_by_build(classification_path: Path) -> dict[str, str]:
    raw = json.loads(classification_path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for build_id, entry in raw.items():
        code = entry.get("category")
        if code and code in CODE_TO_L1:
            out[build_id] = CODE_TO_L1[code]
    return out


def get_comparable_build_ids(
    model_statuses: dict[str, dict[str, str]],
    baseline: str,
) -> list[str]:
    if baseline not in model_statuses:
        raise KeyError(f"Baseline model {baseline!r} not in loaded statuses")
    return [
        bid
        for bid, status in model_statuses[baseline].items()
        if status != "skipped"
    ]


def collect_stats(
    model_statuses: dict[str, dict[str, str]],
    build_l1: dict[str, str],
    comparable_ids: list[str],
) -> dict[str, dict[str, dict[str, int | float | None]]]:
    """Compute stats on the same comparable build subset; stats[l1][model] = {success, total, rate}; total is identical per row."""
    l1_builds: dict[str, list[str]] = defaultdict(list)
    for bid in comparable_ids:
        l1 = build_l1.get(bid)
        if l1:
            l1_builds[l1].append(bid)

    stats: dict[str, dict[str, dict]] = {}
    for l1 in L1_ORDER + ["All"]:
        stats[l1] = {}
        build_ids = list(comparable_ids) if l1 == "All" else l1_builds.get(l1, [])
        total = len(build_ids)
        for model, statuses in model_statuses.items():
            success = sum(
                1
                for bid in build_ids
                if statuses.get(bid) == "success"
            )
            stats[l1][model] = {
                "success": success,
                "total": total,
                "rate": round(100 * success / total, 1) if total else None,
            }
    return stats


def _rate_color(rate: float | None) -> str:
    if rate is None:
        return "#f3f4f6"
    # 0% -> #fee2e2, 50% -> #fef9c3, 100% -> #dcfce7
    t = max(0.0, min(1.0, rate / 100.0))
    if t < 0.5:
        # red to yellow
        u = t / 0.5
        r = int(254 - u * (254 - 254))
        g = int(226 + u * (249 - 226))
        b = int(226 + u * (195 - 226))
    else:
        u = (t - 0.5) / 0.5
        r = int(254 - u * (254 - 220))
        g = int(249 + u * (252 - 249))
        b = int(195 + u * (231 - 195))
    return f"rgb({r},{g},{b})"


def render_html(
    stats: dict,
    *,
    models: list[str],
    baseline: str,
    comparable_total: int,
    classification_path: str,
    model_paths: dict[str, str],
) -> str:
    rows_html = []
    for l1 in L1_ORDER + ["All"]:
        row_class = "row-all" if l1 == "All" else ""
        n = stats[l1][models[0]]["total"]
        cells = [
            f'<td class="l1 {row_class}">{escape(l1)}</td>',
            f'<td class="n-col">{n}</td>',
        ]
        for model in models:
            s = stats[l1][model]
            rate = s["rate"]
            bg = _rate_color(rate)
            is_baseline = model == baseline
            th_cls = ' class="baseline-col"' if is_baseline else ""
            if s["total"] == 0:
                cell = '<span class="muted">—</span>'
            else:
                cell = (
                    f'<strong>{rate:.1f}%</strong>'
                    f'<br><span class="sub">{s["success"]}/{s["total"]}</span>'
                )
            cells.append(
                f'<td class="num{th_cls}" style="background:{bg}">{cell}</td>'
            )
        rows_html.append(f'<tr class="{row_class}">' + "".join(cells) + "</tr>")

    header_cells = "".join(
        f'<th class="{"baseline-col" if m == baseline else ""}">'
        f'{escape(m)}{" *" if m == baseline else ""}</th>'
        for m in models
    )
    model_list = "".join(f"<li><code>{escape(p)}</code></li>" for p in model_paths.values())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DoFix model repair rate by L1 failure type</title>
  <style>
    :root {{
      --border: #d1d5db;
      --text: #111827;
      --muted: #6b7280;
    }}
    body {{
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      color: var(--text);
      margin: 2rem;
      line-height: 1.5;
    }}
    h1 {{ font-size: 1.35rem; margin-bottom: 0.25rem; }}
    .meta {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 1.25rem; }}
    table {{
      border-collapse: collapse;
      width: 100%;
      max-width: 1100px;
      font-size: 0.92rem;
    }}
    th, td {{
      border: 1px solid var(--border);
      padding: 0.55rem 0.75rem;
      text-align: center;
      vertical-align: middle;
    }}
    th {{
      background: #f9fafb;
      font-weight: 600;
    }}
    th.baseline-col, td.baseline-col {{
      box-shadow: inset 0 0 0 2px #2563eb33;
    }}
    td.n-col {{
      font-weight: 600;
      background: #f3f4f6;
      color: var(--muted);
    }}
    td.l1 {{
      text-align: left;
      font-weight: 500;
      min-width: 14rem;
      background: #fafafa;
    }}
    tr.row-all td.l1 {{ font-weight: 700; }}
    tr.row-all td {{ border-top: 2px solid #9ca3af; }}
    .sub {{ color: var(--muted); font-size: 0.8rem; }}
    .muted {{ color: var(--muted); }}
    .legend {{
      margin-top: 1rem;
      font-size: 0.85rem;
      color: var(--muted);
    }}
    .legend-bar {{
      display: inline-block;
      width: 180px;
      height: 12px;
      background: linear-gradient(to right, #fee2e2, #fef9c3, #dcfce7);
      vertical-align: middle;
      margin: 0 0.5rem;
      border: 1px solid var(--border);
    }}
    ul.paths {{ font-size: 0.82rem; color: var(--muted); }}
    code {{ font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>DoFix repair success rate by L1 failure type</h1>
  <p class="meta">
    Classification source: <code>{escape(classification_path)}</code> (L1 mapping in
    <code>methods/analysis/classify.md</code>).
    <strong>Comparable baseline</strong>: {comparable_total} builds in
    <code>{escape(baseline)}</code> with <code>status ≠ skipped</code>; each model reports
    success on the same build subset (denominator N per L1; non-success includes failed / timeout / missed, etc.).
    Column marked * is the reference baseline.
  </p>
  <table>
    <thead>
      <tr>
        <th>L1 Category</th>
        <th>N</th>
        {header_cells}
      </tr>
    </thead>
    <tbody>
      {"".join(rows_html)}
    </tbody>
  </table>
  <p class="legend">
    Color scale: <span class="legend-bar"></span> 0% → 50% → 100%
  </p>
  <h2 style="font-size:1rem;margin-top:1.5rem;">Run logs paths</h2>
  <ul class="paths">
    {model_list}
  </ul>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="L1 repair rate comparison HTML")
    parser.add_argument("--classification", default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--baseline", default=DEFAULT_COMPARE_BASELINE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    classification_path = PROJECT_ROOT / args.classification
    build_l1 = load_l1_by_build(classification_path)

    model_statuses: dict[str, dict[str, str]] = {}
    resolved_paths: dict[str, str] = {}
    for model, rel in DEFAULT_MODEL_RUN_LOGS.items():
        run_dir = PROJECT_ROOT / rel
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Missing run_logs: {run_dir}")
        model_statuses[model] = load_build_status(run_dir)
        resolved_paths[model] = rel

    models = list(DEFAULT_MODEL_RUN_LOGS.keys())
    comparable_ids = get_comparable_build_ids(model_statuses, args.baseline)
    stats = collect_stats(model_statuses, build_l1, comparable_ids)

    out_path = PROJECT_ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(
        stats,
        models=models,
        baseline=args.baseline,
        comparable_total=len(comparable_ids),
        classification_path=args.classification,
        model_paths=resolved_paths,
    )
    out_path.write_text(html, encoding="utf-8")

    print(f"Baseline: {args.baseline} ({len(comparable_ids)} comparable builds)")
    for l1 in L1_ORDER + ["All"]:
        n = stats[l1][models[0]]["total"]
        row = {m: f"{stats[l1][m]['success']}/{n} ({stats[l1][m]['rate']}%)" for m in models}
        print(f"  {l1} (N={n}): {row}")
    print(f"Wrote {out_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
