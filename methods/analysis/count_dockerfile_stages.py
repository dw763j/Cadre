# Run: source .venv/bin/activate && python -m methods.analysis.count_dockerfile_stages --help
"""
Count multi-stage build stages (FROM instructions) per Dockerfile in a dataset JSON.

Reads:  results/dataset_valid_fixed_params_with_dockerfile_relative.json
        (field dockerfile_content per build entry)

Writes: results/dockerfile_stage_counts.json          (per-entry detail)
        results/dockerfile_stage_counts_summary.json  (aggregate stats)
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
from collections import Counter
from pathlib import Path

from dockerfile_parse import DockerfileParser


def count_stages(dockerfile_content: str) -> tuple[int | None, str | None]:
    """Return (stage_count, error). stage_count is number of FROM instructions."""
    if not dockerfile_content or not dockerfile_content.strip():
        return None, "empty dockerfile_content"
    try:
        dfp = DockerfileParser(fileobj=io.BytesIO(dockerfile_content.encode()))
        from_count = sum(1 for item in dfp.structure if item.get("instruction") == "FROM")
        return from_count, None
    except Exception as e:
        return None, str(e)


def compute_summary(stage_counts: list[int]) -> dict:
    if not stage_counts:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "stdev": None,
            "distribution": {},
        }
    dist = dict(sorted(Counter(stage_counts).items()))
    return {
        "count": len(stage_counts),
        "min": min(stage_counts),
        "max": max(stage_counts),
        "mean": round(statistics.mean(stage_counts), 4),
        "median": statistics.median(stage_counts),
        "stdev": round(statistics.stdev(stage_counts), 4) if len(stage_counts) > 1 else 0.0,
        "distribution": {str(k): v for k, v in dist.items()},
        "single_stage_count": dist.get(1, 0),
        "multi_stage_count": sum(v for k, v in dist.items() if k >= 2),
        "single_stage_pct": round(100 * dist.get(1, 0) / len(stage_counts), 2),
        "multi_stage_pct": round(100 * sum(v for k, v in dist.items() if k >= 2) / len(stage_counts), 2),
    }


def analyze_dataset(dataset_path: Path) -> tuple[dict, dict]:
    with dataset_path.open(encoding="utf-8") as f:
        data = json.load(f)

    per_entry: dict = {}
    stage_counts: list[int] = []
    empty_count = 0
    parse_error_count = 0
    parse_errors: list[dict] = []

    for repo, jobs in data.items():
        for build_id, entry in jobs.items():
            content = entry.get("dockerfile_content", "")
            stage_count, err = count_stages(content)
            dockerfile_file = (entry.get("build_params") or {}).get("file", "")

            record = {
                "repo": repo,
                "build_id": build_id,
                "dockerfile_file": dockerfile_file,
                "stage_count": stage_count,
                "error": err,
            }
            per_entry[build_id] = record

            if err == "empty dockerfile_content":
                empty_count += 1
            elif err:
                parse_error_count += 1
                parse_errors.append({"build_id": build_id, "repo": repo, "error": err})
            elif stage_count is not None:
                stage_counts.append(stage_count)

    summary = {
        "dataset": str(dataset_path),
        "total_entries": len(per_entry),
        "parsed_ok": len(stage_counts),
        "empty_dockerfile": empty_count,
        "parse_errors": parse_error_count,
        "stage_stats": compute_summary(stage_counts),
        "parse_error_samples": parse_errors[:20],
    }
    return per_entry, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Count Dockerfile stages (FROM) in dataset JSON")
    parser.add_argument(
        "--dataset",
        default="results/dataset_valid_fixed_params_with_dockerfile_relative.json",
        help="Input dataset JSON path",
    )
    parser.add_argument(
        "--output",
        default="results/dockerfile_stage_counts.json",
        help="Per-entry output JSON path",
    )
    parser.add_argument(
        "--summary",
        default="results/dockerfile_stage_counts_summary.json",
        help="Summary statistics JSON path",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    output_path = Path(args.output)
    summary_path = Path(args.summary)

    per_entry, summary = analyze_dataset(dataset_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(per_entry, f, ensure_ascii=False, indent=2)
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    stats = summary["stage_stats"]
    print(f"Dataset: {dataset_path}")
    print(f"Total entries: {summary['total_entries']}")
    print(f"Parsed OK: {summary['parsed_ok']} | empty: {summary['empty_dockerfile']} | parse errors: {summary['parse_errors']}")
    if stats["count"]:
        print(f"Stages — min: {stats['min']}, max: {stats['max']}, mean: {stats['mean']}, median: {stats['median']}")
        print(f"Single-stage: {stats['single_stage_count']} ({stats['single_stage_pct']}%)")
        print(f"Multi-stage:  {stats['multi_stage_count']} ({stats['multi_stage_pct']}%)")
        print("Distribution:", stats["distribution"])
    print(f"Wrote detail  -> {output_path}")
    print(f"Wrote summary -> {summary_path}")


if __name__ == "__main__":
    main()
