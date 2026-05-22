# Run: source .venv/bin/activate && python -m methods.analyze_results.cadre_unique_success
"""Find builds where Cadre succeeds and all other baselines fail (within the 653-build comparable set)."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import PROJECT_ROOT

from methods.analyze_results.comparable_results import get_unified_comparable_results
from methods.analyze_results.repair_rate_by_l1_html import CODE_TO_L1
from methods.analyze_results.results_by_stage import DEFAULT_COMPARE_TOOL, DEFAULT_RESULT_PATHS

DEFAULT_CLASSIFICATION = "results/failure_classification_dataset.json"
DEFAULT_JSON_OUT = "results/analysis/cadre_unique_success.json"
DEFAULT_TXT_OUT = "results/analysis/cadre_unique_success.txt"


def find_cadre_unique_success(
    compare_result: dict[str, dict],
    *,
    compare_tool: str = DEFAULT_COMPARE_TOOL,
    other_methods: list[str] | None = None,
    strict_all_others_fail: bool = True,
) -> list[dict]:
    """strict=True: all other methods non-success; False: at least one other method non-success."""
    others = other_methods or [m for m in DEFAULT_RESULT_PATHS if m != compare_tool]
    comparable = [
        bid
        for bid, st in compare_result[compare_tool].items()
        if bid not in ("summary", "common_success") and st != "skipped"
    ]

    records: list[dict] = []
    for bid in comparable:
        if compare_result[compare_tool].get(bid) != "success":
            continue
        other_status = {m: compare_result[m].get(bid, "missed") for m in others if m in compare_result}
        non_success = {m: st for m, st in other_status.items() if st != "success"}
        if strict_all_others_fail:
            if len(non_success) != len(other_status):
                continue
        elif not non_success:
            continue
        records.append({"build_id": bid, "others": other_status})

    return records


def enrich_records(records: list[dict], classification_path: Path) -> list[dict]:
    cls = json.loads(classification_path.read_text(encoding="utf-8"))
    out = []
    for r in records:
        bid = r["build_id"]
        cat = cls.get(bid, {}).get("category", "")
        out.append(
            {
                "build_id": bid,
                "repo": "#".join(bid.split("#")[:2]) if bid.count("#") >= 2 else bid,
                "category": cat,
                "l1": CODE_TO_L1.get(cat, ""),
                "cadre": "success",
                "others": r["others"],
            }
        )
    return out


def write_txt(path: Path, records: list[dict], *, compare_tool: str, strict: bool) -> None:
    lines = [
        f"# Cadre-only success (compare={compare_tool}, strict_all_others_fail={strict})",
        f"# count={len(records)}",
        "",
    ]
    for i, r in enumerate(records, 1):
        others = ", ".join(f"{m}={s}" for m, s in r["others"].items())
        lines.append(f"{i:3d}. [{r['category']}] {r['l1']}")
        lines.append(f"     {r['build_id']}")
        lines.append(f"     {others}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="List builds where only Cadre succeeds")
    parser.add_argument("--classification", default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--json-out", default=DEFAULT_JSON_OUT)
    parser.add_argument("--txt-out", default=DEFAULT_TXT_OUT)
    parser.add_argument(
        "--relaxed",
        action="store_true",
        help="Cadre success and at least one other method non-success (default: all other methods non-success)",
    )
    args = parser.parse_args()

    _, compare_result = get_unified_comparable_results(
        DEFAULT_RESULT_PATHS,
        compare_tool=DEFAULT_COMPARE_TOOL,
    )
    strict = not args.relaxed
    raw = find_cadre_unique_success(compare_result, strict_all_others_fail=strict)
    records = enrich_records(raw, PROJECT_ROOT / args.classification)

    payload = {
        "compare_tool": DEFAULT_COMPARE_TOOL,
        "other_methods": [m for m in DEFAULT_RESULT_PATHS if m != DEFAULT_COMPARE_TOOL],
        "mode": "strict_all_others_fail" if strict else "at_least_one_other_fail",
        "count": len(records),
        "by_category": dict(Counter(r["category"] for r in records).most_common()),
        "by_l1": dict(Counter(r["l1"] for r in records).most_common()),
        "builds": records,
    }

    json_path = PROJECT_ROOT / args.json_out
    txt_path = PROJECT_ROOT / args.txt_out
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_txt(txt_path, records, compare_tool=DEFAULT_COMPARE_TOOL, strict=strict)

    print(f"Mode: {payload['mode']}")
    print(f"Count: {len(records)}")
    print(f"By L1: {payload['by_l1']}")
    print(f"Wrote {json_path.relative_to(PROJECT_ROOT)}")
    print(f"Wrote {txt_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
