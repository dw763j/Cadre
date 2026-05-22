"""
Compute lightweight evaluation metrics for Dockerfile repair results:
  - SPV  (Static Patch Validity):      Can the repaired Dockerfile be parsed without error?
  - CRS  (Context Relevance Score):    Jaccard between LLM-requested diff files and actual diff files.
  - PMS  (Patch Minimality Score):     1 - (changed lines / original lines); higher = more targeted patch.

Usage:
    conda run -n base python -m methods.analysis.compute_metrics \
        --repair_dir results/repairing_dockerfile/dofix/standard/DeepSeek-V3/repair_results_dofix \
        --dataset    results/dataset_valid_fixed_params_with_dockerfile.json \
        --output     results/metrics_dofix.json
"""

import argparse
import difflib
import io
import json
import os
import re
from pathlib import Path

from dockerfile_parse import DockerfileParser


# ---------------------------------------------------------------------------
# SPV
# ---------------------------------------------------------------------------

def compute_spv(dockerfile_content: str) -> dict:
    """Return {'valid': bool, 'error': str|None}."""
    try:
        dfp = DockerfileParser(fileobj=io.BytesIO(dockerfile_content.encode()))
        _ = dfp.structure  # triggers actual parsing
        return {"valid": True, "error": None}
    except Exception as e:
        return {"valid": False, "error": str(e)}


# ---------------------------------------------------------------------------
# CRS
# ---------------------------------------------------------------------------

def _normalise_path(p: str) -> str:
    """Strip leading ./ and normalise separators."""
    return re.sub(r"^\./", "", p.strip().replace("\\", "/"))


def compute_crs(key_files_response: dict, diff_info: dict) -> dict:
    """
    key_files_response: the LLM's structured response from the Context Inquiry step.
        Fields: new_files, modified_files, deleted_files  (LLM-selected diff files)
    diff_info: dict keyed by file path of every file changed in the failing commit.

    Returns {'crs': float, 'requested': list, 'actual': list}.
    """
    if not diff_info:
        return {"crs": None, "requested": [], "actual": [], "note": "no diff_info"}

    actual = set(_normalise_path(p) for p in diff_info.keys())

    requested = set()
    for field in ("new_files", "modified_files", "deleted_files"):
        for p in key_files_response.get(field, []):
            requested.add(_normalise_path(p))

    if not requested and not actual:
        return {"crs": None, "requested": [], "actual": list(actual), "note": "empty sets"}

    intersection = requested & actual
    union = requested | actual
    crs = len(intersection) / len(union) if union else None

    return {
        "crs": round(crs, 4) if crs is not None else None,
        "requested": sorted(requested),
        "actual": sorted(actual),
        "intersection_count": len(intersection),
        "union_count": len(union),
    }


# ---------------------------------------------------------------------------
# PMS
# ---------------------------------------------------------------------------

def compute_pms(original: str, repaired: str) -> dict:
    """
    Patch Minimality Score = 1 - (changed_lines / max(original_lines, 1)).
    Changed lines = lines that appear only in the diff (insertions + deletions).
    Higher score means a more surgical, minimal patch.
    """
    if not original or not repaired:
        return {"pms": None, "changed_lines": None, "original_lines": None}

    orig_lines = original.splitlines()
    rep_lines = repaired.splitlines()

    diff = list(difflib.unified_diff(orig_lines, rep_lines, lineterm=""))
    changed = sum(1 for line in diff if line.startswith("+") or line.startswith("-"))
    # subtract the +++ / --- header lines
    changed = max(0, changed - 2)

    orig_len = max(len(orig_lines), 1)
    pms = max(0.0, 1.0 - changed / orig_len)

    return {
        "pms": round(pms, 4),
        "changed_lines": changed,
        "original_lines": len(orig_lines),
    }


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------

def load_dataset(dataset_path: str) -> dict:
    with open(dataset_path, encoding="utf-8") as f:
        return json.load(f)


def build_lookup(dataset: dict) -> dict:
    """Build flat dict: full_build_id -> build_info."""
    lookup = {}
    for builds in dataset.values():
        for full_build_id, info in builds.items():
            lookup[full_build_id] = info
    return lookup


def run(repair_dir: str, dataset_path: str, output_path: str):
    dataset = load_dataset(dataset_path)
    lookup = build_lookup(dataset)

    all_results = {}
    spv_valid = spv_total = 0
    crs_values = []
    pms_values = []

    repair_files = list(Path(repair_dir).glob("*.json"))
    print(f"Found {len(repair_files)} repair result files in {repair_dir}")

    for rf in repair_files:
        with open(rf, encoding="utf-8") as f:
            repo_results = json.load(f)

        for full_build_id, sub in repo_results.items():
            build_info = lookup.get(full_build_id)
            if build_info is None:
                continue

            diff_info = build_info.get("diff_info") or {}
            original_df = build_info.get("dockerfile_content", "")

            for repair_key, entry in sub.items():
                if not isinstance(entry, dict):
                    continue

                repaired_df = entry.get("repaired_dockerfile", "")
                kfr = entry.get("key_files_response", {})

                metrics = {}

                # SPV
                if repaired_df:
                    spv = compute_spv(repaired_df)
                    metrics["spv"] = spv
                    spv_total += 1
                    if spv["valid"]:
                        spv_valid += 1
                else:
                    metrics["spv"] = {"valid": None, "error": "no repaired_dockerfile"}

                # CRS
                if isinstance(kfr, dict):
                    crs = compute_crs(kfr, diff_info)
                    metrics["crs"] = crs
                    if crs.get("crs") is not None:
                        crs_values.append(crs["crs"])
                else:
                    metrics["crs"] = {"crs": None, "note": "kfr not dict"}

                # PMS
                if repaired_df and original_df:
                    pms = compute_pms(original_df, repaired_df)
                    metrics["pms"] = pms
                    if pms.get("pms") is not None:
                        pms_values.append(pms["pms"])
                else:
                    metrics["pms"] = {"pms": None, "note": "missing original or repaired"}

                all_results.setdefault(full_build_id, {})[repair_key] = metrics

    # Summary statistics
    summary = {
        "total_repairs_evaluated": spv_total,
        "spv": {
            "valid_count": spv_valid,
            "invalid_count": spv_total - spv_valid,
            "validity_rate": round(spv_valid / spv_total, 4) if spv_total else None,
        },
        "crs": {
            "count": len(crs_values),
            "mean": round(sum(crs_values) / len(crs_values), 4) if crs_values else None,
            "median": round(sorted(crs_values)[len(crs_values) // 2], 4) if crs_values else None,
            "zero_rate": round(sum(1 for v in crs_values if v == 0) / len(crs_values), 4) if crs_values else None,
        },
        "pms": {
            "count": len(pms_values),
            "mean": round(sum(pms_values) / len(pms_values), 4) if pms_values else None,
            "median": round(sorted(pms_values)[len(pms_values) // 2], 4) if pms_values else None,
        },
    }

    output = {"summary": summary, "per_case": all_results}

    os.makedirs(Path(output_path).parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print("\n=== Metric Summary ===")
    print(json.dumps(summary, indent=2))
    print(f"\nFull results saved to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute SPV / CRS / PMS metrics")
    parser.add_argument("--repair_dir", required=True, help="Directory containing repair result JSON files")
    parser.add_argument("--dataset", required=True, help="Path to dataset_valid_fixed_params_with_dockerfile.json")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    run(args.repair_dir, args.dataset, args.output)
