#!/usr/bin/env python3
"""
Scan results JSON files (produced by get_dataset/get_diff.py) and list changed files
whose paths include "Dockerfile" (case-insensitive by default).

Usage:
  python -m utils.find_dockerfile_changes \\
    --results-dir results/push_failed_job_diffs \\
    [--case-sensitive] [--output csv|json]
"""

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from config import RESULTS_DIR


def collect_result_files(results_dir: Path) -> list[Path]:
    if not results_dir.exists() or not results_dir.is_dir():
        raise FileNotFoundError(f"Results directory not found: {results_dir}")
    return sorted(results_dir.glob("*.json"))


def is_dockerfile_path(path_str: str, case_sensitive: bool) -> bool:
    if not path_str:
        return False
    haystack = path_str if case_sensitive else path_str.lower()
    needle = "Dockerfile" if case_sensitive else "dockerfile"
    return needle in haystack


def iter_matches(files: Iterable[Path], case_sensitive: bool) -> Iterable[dict[str, Any]]:
    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"WARN: failed to read {file_path}: {exc}", file=sys.stderr)
            continue

        run_folder = data.get("run_folder")
        commit_id = data.get("commit_id")
        diff_files: dict[str, Any] = data.get("diff_files", {})

        for entry_key, entry in diff_files.items():
            # Prefer explicit new/old paths if present, otherwise fallback to dict key
            old_path = entry.get("old_path") or entry_key
            new_path = entry.get("new_path") or entry_key
            file_status = entry.get("file_status", "unknown")

            # Check both old and new paths for safety (renames/moves)
            if is_dockerfile_path(str(old_path), case_sensitive) or is_dockerfile_path(str(new_path), case_sensitive):
                yield {
                    "result_file": str(file_path),
                    "run_folder": run_folder,
                    "commit_id": commit_id,
                    "file_status": file_status,
                    "old_path": old_path,
                    "new_path": new_path,
                }


def print_csv(rows: Iterable[dict[str, Any]]) -> None:
    import csv

    fieldnames = [
        "run_folder",
        "commit_id",
        "file_status",
        "old_path",
        "new_path",
        "result_file",
    ]
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in fieldnames})


def print_json(rows: Iterable[dict[str, Any]]) -> None:
    rows_list = list(rows)
    
    print(json.dumps(rows_list, ensure_ascii=False, indent=2))
    files = set()
    for row in rows_list:
        files.add(row["result_file"])
    print(f"Found {len(rows_list)} matches in {len(files)} files")



def main() -> None:
    parser = argparse.ArgumentParser(description="List changed Dockerfile paths from results JSONs")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR / "push_failed_job_diffs",
        help="Directory containing results JSON files",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Match 'Dockerfile' in a case-sensitive manner (default: case-insensitive)",
    )
    parser.add_argument(
        "--output",
        choices=["csv", "json"],
        default="json",
        help="Output format",
    )
    args = parser.parse_args()

    files = collect_result_files(args.results_dir)
    rows_iter = iter_matches(files, case_sensitive=args.case_sensitive)

    if args.output == "csv":
        print_csv(rows_iter)
    else:
        print_json(rows_iter)


if __name__ == "__main__":
    main()


