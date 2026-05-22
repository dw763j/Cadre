# Run: cd project root && source .venv/bin/activate && python -m methods.analyze_results.cadre_success_others_failed_html
# All baselines failed: python -m methods.analyze_results.cadre_success_others_failed_html
# Vanilla + FlakiDock-DS-V3 failed:
#   python -m methods.analyze_results.cadre_success_others_failed_html \
#     --require-failed Vanilla-LLM,FlakiDock-DS-V3 \
#     --diff-methods Cadre,Vanilla-LLM,FlakiDock-DS-V3 \
#     --output results/analysis/cadre_success_vanilla_flakidock_ds_failed.html \
#     --page-title "Cadre success · Vanilla-LLM & FlakiDock-DS-V3 failed"
"""Generate HTML comparing cases where Cadre succeeds and specified baselines fail (with diff visualization)."""
from __future__ import annotations

import argparse
import difflib
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import PROJECT_ROOT

from methods.analyze_results.comparable_results import get_unified_comparable_results
from methods.analyze_results.repair_rate_by_l1_html import CODE_TO_L1
from methods.analyze_results.results_by_stage import DEFAULT_COMPARE_TOOL, DEFAULT_RESULT_PATHS

DEFAULT_DATASET = "results/dataset_valid_fixed_params_with_dockerfile_relative.json"
DEFAULT_CLASSIFICATION = "results/failure_classification_dataset.json"
DEFAULT_OUTPUT = "results/analysis/cadre_success_others_failed.html"

FIXED_DOCKERFILE_DIRS: dict[str, tuple[str, str]] = {
    "Cadre": (
        "results/fixed_dockerfiles/dofix/standard/DeepSeek-V3",
        "{bid}#dofix#DeepSeek-V3",
    ),
    "Parfum": ("results/fixed_dockerfiles/parfum", "{bid}#parfum"),
    "FlakiDock": (
        "results/ablation/fixed_dockerfiles_flakidock_gpt-4",
        "{bid}#flakidock#gpt-4",
    ),
    "FlakiDock-DS-V3": (
        "results/ablation/fixed_dockerfiles_flakidock_upgrade_response_deal_DeepSeek-V3",
        "{bid}#flakidock#DeepSeek-V3",
    ),
    "Vanilla-LLM": (
        "results/ablation/fixed_dockerfiles_pure_llm_DeepSeek-V3",
        "{bid}#pure-llm#DeepSeek-V3",
    ),
}

METHOD_ORDER = [
    "Cadre",
    "Parfum",
    "FlakiDock",
    "FlakiDock-DS-V3",
    "Vanilla-LLM",
]


def repo_key_from_build_id(build_id: str) -> str:
    parts = build_id.split("#")
    return "#".join(parts[:2]) if len(parts) >= 2 else build_id


def find_filtered_records(
    compare_result: dict[str, dict],
    *,
    require_failed: list[str] | None = None,
    require_all_others_failed: bool = False,
) -> list[dict]:
    """Filter builds where Cadre=success and each method in require_failed is failed."""
    others = [m for m in DEFAULT_RESULT_PATHS if m != DEFAULT_COMPARE_TOOL]
    comparable = [
        bid
        for bid, st in compare_result[DEFAULT_COMPARE_TOOL].items()
        if bid not in ("summary", "common_success") and st != "skipped"
    ]
    must_fail = require_failed
    if must_fail is None and require_all_others_failed:
        must_fail = others

    records: list[dict] = []
    for bid in comparable:
        if compare_result[DEFAULT_COMPARE_TOOL].get(bid) != "success":
            continue
        other_status = {m: compare_result[m].get(bid, "missed") for m in others if m in compare_result}
        if must_fail:
            if not all(other_status.get(m) == "failed" for m in must_fail):
                continue
        records.append({"build_id": bid, "others": other_status})
    records.sort(key=lambda r: r["build_id"])
    return records


def load_dataset_index(dataset_path: Path) -> dict[str, dict]:
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    for _repo_key, builds in raw.items():
        if not isinstance(builds, dict):
            continue
        for bid, info in builds.items():
            if isinstance(info, dict):
                index[bid] = info
    return index


def read_text_file(path: Path) -> str:
    if not path.is_file():
        return f"(file not found: {path.relative_to(PROJECT_ROOT)})"
    return path.read_text(encoding="utf-8", errors="replace")


def load_build_context(
    bid: str,
    dataset_index: dict[str, dict],
    classification: dict[str, dict],
) -> dict:
    build = dataset_index.get(bid, {})
    cat = classification.get(bid, {}).get("category", "")
    error_log = build.get("error_log") or ""
    if not error_log and build.get("error_log_path"):
        elp = Path(build["error_log_path"])
        if not elp.is_absolute():
            elp = PROJECT_ROOT / elp
        if elp.is_file():
            error_log = elp.read_text(encoding="utf-8", errors="replace")

    fixed: dict[str, str] = {}
    for method in METHOD_ORDER:
        base_rel, stem_tpl = FIXED_DOCKERFILE_DIRS[method]
        stem = stem_tpl.format(bid=bid)
        fixed[method] = read_text_file(PROJECT_ROOT / base_rel / stem)

    return {
        "build_id": bid,
        "repo": repo_key_from_build_id(bid),
        "category": cat,
        "l1": CODE_TO_L1.get(cat, ""),
        "error_log": error_log or "(no error_log)",
        "dockerfile_original": build.get("dockerfile_content") or "(no original Dockerfile)",
        "fixed": fixed,
    }


def pre_block(text: str) -> str:
    return f'<pre class="code">{html.escape(text)}</pre>'


def _as_lines(text: str) -> list[str]:
    if not text:
        return []
    lines = text.splitlines()
    return lines if lines else [text]


def render_dockerfile_diff(original: str, fixed: str, *, method: str) -> str:
    """Side-by-side diff of original vs repaired Dockerfile (difflib.HtmlDiff)."""
    if original.startswith("(") and "Dockerfile" in original:
        return '<p class="diff-empty">Cannot diff: missing original Dockerfile</p>'
    if fixed.startswith("(file not found"):
        return f'<p class="diff-empty">{html.escape(fixed)}</p>'
    if original == fixed:
        return '<p class="diff-empty">Identical to original Dockerfile (no changes)</p>'

    differ = difflib.HtmlDiff(wrapcolumn=88, tabsize=2)
    table = differ.make_table(
        _as_lines(original),
        _as_lines(fixed),
        fromdesc="Original",
        todesc=method,
        context=True,
        numlines=3,
    )
    return f'<div class="diff-wrap">{table}</div>'


def render_case(
    case: dict,
    index: int,
    statuses: dict[str, str],
    *,
    diff_methods: list[str],
) -> str:
    pills = [f'<span class="pill ok">{html.escape(DEFAULT_COMPARE_TOOL)}: success</span>']
    for m, st in statuses.items():
        cls = "fail" if st == "failed" else "neutral"
        pills.append(f'<span class="pill {cls}">{html.escape(m)}: {html.escape(st)}</span>')

    fixed_sections = []
    for method in diff_methods:
        open_attr = " open" if method == "Cadre" else ""
        diff_html = render_dockerfile_diff(
            case["dockerfile_original"], case["fixed"][method], method=method
        )
        fixed_sections.append(
            f'<details class="fix-panel"{open_attr}>'
            f"<summary>{html.escape(method)} · diff vs original</summary>"
            f"{diff_html}"
            f"</details>"
        )

    meta_parts = []
    if case["category"]:
        meta_parts.append(f'L2: <code>{html.escape(case["category"])}</code>')
    if case["l1"]:
        meta_parts.append(f"L1: {html.escape(case['l1'])}")

    return f"""
<article class="case" id="case-{index}">
  <header>
    <h2><a href="#case-{index}">#{index}</a> <code>{html.escape(case['build_id'])}</code></h2>
    <p class="repo">{html.escape(case['repo'])}</p>
    <p class="meta-pills">{''.join(pills)}</p>
    <p class="meta-cat">{' · '.join(meta_parts) if meta_parts else ''}</p>
  </header>
  <details class="section" open>
    <summary>Original build error log</summary>
    {pre_block(case['error_log'])}
  </details>
  <details class="section">
    <summary>Original Dockerfile</summary>
    {pre_block(case['dockerfile_original'])}
  </details>
  <div class="fixes">
    <h3>Repair diffs by method (left: original, right: repaired)</h3>
    {''.join(fixed_sections)}
  </div>
</article>
"""


def render_html(
    cases: list[dict],
    records: list[dict],
    *,
    page_title: str,
    page_description: str,
    diff_methods: list[str],
) -> str:
    nav_items = []
    case_html = []
    for i, (rec, case) in enumerate(zip(records, cases), 1):
        statuses = rec["others"]
        nav_label = case.get("l1") or case.get("category") or ""
        nav_items.append(
            f'<li><a href="#case-{i}">{html.escape(case["build_id"])}</a>'
            f' <span class="muted">({html.escape(nav_label)})</span></li>'
        )
        case_html.append(render_case(case, i, statuses, diff_methods=diff_methods))

    model_paths = "".join(
        f"<li><strong>{html.escape(m)}</strong>: <code>{html.escape(base)}</code> / "
        f"<code>{html.escape(stem)}</code></li>"
        for m, (base, stem) in FIXED_DOCKERFILE_DIRS.items()
        if m in diff_methods
    )

    run_logs = "".join(
        f"<li><strong>{html.escape(m)}</strong>: <code>{html.escape(p)}</code></li>"
        for m, p in DEFAULT_RESULT_PATHS.items()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(page_title)}</title>
  <style>
    :root {{
      --border: #d1d5db;
      --text: #111827;
      --muted: #6b7280;
      --ok: #166534;
      --fail: #991b1b;
      --ok-bg: #dcfce7;
      --fail-bg: #fee2e2;
    }}
    body {{
      font-family: "Segoe UI", system-ui, sans-serif;
      color: var(--text);
      margin: 0;
      line-height: 1.45;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 280px 1fr;
      min-height: 100vh;
    }}
    nav {{
      border-right: 1px solid var(--border);
      padding: 1rem;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      background: #fafafa;
      font-size: 0.82rem;
    }}
    nav h1 {{ font-size: 0.95rem; margin: 0 0 0.5rem; }}
    nav ul {{ list-style: none; padding: 0; margin: 0; }}
    nav li {{ margin-bottom: 0.35rem; word-break: break-all; }}
    main {{ padding: 1.25rem 1.5rem 3rem; max-width: 1200px; }}
    h1.page-title {{ font-size: 1.25rem; margin: 0 0 0.35rem; }}
    .page-meta {{ color: var(--muted); font-size: 0.88rem; margin-bottom: 1.25rem; }}
    .case {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem 1.1rem;
      margin-bottom: 1.5rem;
      background: #fff;
    }}
    .case h2 {{ font-size: 1rem; margin: 0; }}
    .case h2 a {{ color: inherit; text-decoration: none; }}
    .repo {{ color: var(--muted); font-size: 0.85rem; margin: 0.2rem 0 0.5rem; }}
    .meta-pills {{ margin: 0.35rem 0; display: flex; flex-wrap: wrap; gap: 0.35rem; }}
    .pill {{
      font-size: 0.75rem;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      border: 1px solid var(--border);
    }}
    .pill.ok {{ background: var(--ok-bg); color: var(--ok); border-color: #86efac; }}
    .pill.fail {{ background: var(--fail-bg); color: var(--fail); border-color: #fca5a5; }}
    .pill.neutral {{
      background: #f3f4f6;
      color: #4b5563;
      border-color: #d1d5db;
    }}
    .meta-cat {{ font-size: 0.82rem; color: var(--muted); margin: 0; }}
    details.section, details.fix-panel {{
      margin-top: 0.75rem;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0 0.6rem 0.6rem;
    }}
    details > summary {{
      cursor: pointer;
      font-weight: 600;
      font-size: 0.9rem;
      padding: 0.45rem 0.15rem;
    }}
    .fixes h3 {{ font-size: 0.95rem; margin: 1rem 0 0.35rem; }}
    pre.code {{
      margin: 0.35rem 0 0;
      padding: 0.65rem 0.75rem;
      background: #f8fafc;
      border: 1px solid #e5e7eb;
      border-radius: 4px;
      font-size: 0.78rem;
      line-height: 1.35;
      overflow: auto;
      max-height: 420px;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .diff-wrap {{
      margin-top: 0.35rem;
      overflow: auto;
      max-height: 520px;
      border: 1px solid #e5e7eb;
      border-radius: 4px;
      background: #fff;
    }}
    .diff-wrap table.diff {{
      width: 100%;
      border-collapse: collapse;
      font-family: ui-monospace, "Cascadia Code", "SF Mono", Consolas, monospace;
      font-size: 0.74rem;
      line-height: 1.3;
    }}
    .diff-wrap .diff_header {{
      background: #f3f4f6;
      color: var(--muted);
      font-weight: 600;
      padding: 0.35rem 0.5rem;
      border-bottom: 1px solid var(--border);
    }}
    .diff-wrap .diff_next {{
      background: #f9fafb;
    }}
    .diff-wrap .diff_add {{
      background: #ecfdf5;
    }}
    .diff-wrap .diff_sub {{
      background: #fef2f2;
    }}
    .diff-wrap .diff_chg {{
      background: #fffbeb;
    }}
    .diff-wrap td {{
      padding: 0 0.35rem;
      vertical-align: top;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .diff-wrap td:first-child {{
      color: #9ca3af;
      text-align: right;
      user-select: none;
      width: 2.5rem;
    }}
    .diff-empty {{
      margin: 0.35rem 0 0;
      font-size: 0.82rem;
      color: var(--muted);
      font-style: italic;
    }}
    .muted {{ color: var(--muted); }}
    ul.paths {{ font-size: 0.8rem; color: var(--muted); padding-left: 1.1rem; }}
    code {{ font-size: 0.78rem; }}
    @media (max-width: 900px) {{
      .layout {{ grid-template-columns: 1fr; }}
      nav {{ position: static; height: auto; border-right: none; border-bottom: 1px solid var(--border); }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <nav>
      <h1>Case index ({len(cases)})</h1>
      <ul>
        {''.join(nav_items)}
      </ul>
    </nav>
    <main>
      <h1 class="page-title">{html.escape(page_title)}</h1>
      <p class="page-meta">{page_description}</p>
      <h2 style="font-size:0.95rem;">Run logs</h2>
      <ul class="paths">{run_logs}</ul>
      <h2 style="font-size:0.95rem;">Fixed Dockerfile paths</h2>
      <ul class="paths">{model_paths}</ul>
      {''.join(case_html)}
    </main>
  </div>
</body>
</html>
"""


def _parse_method_list(raw: str | None, default: list[str]) -> list[str]:
    if not raw:
        return default
    names = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [n for n in names if n not in FIXED_DOCKERFILE_DIRS]
    if unknown:
        raise SystemExit(f"Unknown method name: {unknown}; available: {list(FIXED_DOCKERFILE_DIRS)}")
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--classification", default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--require-failed",
        default="",
        help="Methods that must be failed, comma-separated; empty with no --all-others-failed means all other baselines",
    )
    parser.add_argument(
        "--all-others-failed",
        action="store_true",
        help="Require Parfum/FlakiDock/FlakiDock-DS-V3/Vanilla-LLM all failed (default behavior)",
    )
    parser.add_argument(
        "--diff-methods",
        default="",
        help="Methods to show diffs for, comma-separated; default matches --require-failed, else all five",
    )
    parser.add_argument("--page-title", default="Cadre success · all other baselines failed")
    parser.add_argument("--page-description", default="")
    args = parser.parse_args()

    require_failed = _parse_method_list(args.require_failed, []) if args.require_failed else None
    all_others = args.all_others_failed or (require_failed is None and not args.require_failed)
    if all_others:
        require_failed = None

    diff_methods = _parse_method_list(
        args.diff_methods,
        require_failed if require_failed else METHOD_ORDER,
    )

    _, compare = get_unified_comparable_results(
        DEFAULT_RESULT_PATHS, compare_tool=DEFAULT_COMPARE_TOOL
    )
    records = find_filtered_records(
        compare,
        require_failed=require_failed,
        require_all_others_failed=all_others,
    )
    dataset_index = load_dataset_index(PROJECT_ROOT / args.dataset)
    classification = json.loads((PROJECT_ROOT / args.classification).read_text(encoding="utf-8"))

    cases = [
        load_build_context(rec["build_id"], dataset_index, classification) for rec in records
    ]

    if args.page_description:
        page_desc = args.page_description
    elif all_others:
        page_desc = (
            f"Comparable set: {DEFAULT_COMPARE_TOOL} run_logs with status ≠ skipped; "
            f"filter: {DEFAULT_COMPARE_TOOL} = success, "
            f"all four other methods = failed (excluding skipped / missed). "
            f"Total: <strong>{len(cases)}</strong> builds."
        )
    else:
        failed_names = ", ".join(require_failed or [])
        page_desc = (
            f"Comparable set: {DEFAULT_COMPARE_TOOL} run_logs with status ≠ skipped; "
            f"filter: {DEFAULT_COMPARE_TOOL} = success, "
            f"{failed_names} = failed. Total: <strong>{len(cases)}</strong> builds. "
            f"Diffs shown for: {', '.join(diff_methods)}."
        )

    out_path = PROJECT_ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_html(
            cases,
            records,
            page_title=args.page_title,
            page_description=page_desc,
            diff_methods=diff_methods,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {out_path.relative_to(PROJECT_ROOT)} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
