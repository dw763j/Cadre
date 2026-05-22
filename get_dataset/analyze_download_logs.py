#!/usr/bin/env python3
"""
Analyze extracted log folders vs workflow run conclusions.
"""

import json
import os
from collections import defaultdict, Counter


def parse_folder_name(folder_name: str) -> tuple[str, str, str, str] | None:
    """
    Parse folder owner#repo#repo_id#run_id.
    
    Args:
        folder_name: directory name
        
    Returns:
        (owner, repo, repo_id, run_id) or None
    """
    parts = folder_name.split('#')
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]
    return None


def load_workflow_runs_file(file_path: str) -> dict:
    """
    Load a workflow runs JSON file.
    
    Args:
        file_path: path to JSON
        
    Returns:
        parsed dict
    """
    try:
        with open(file_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return {}


def find_run_status(workflow_data: dict, run_id: str) -> str | None:
    """
    Find conclusion for a run_id.
    
    Args:
        workflow_data: runs payload
        run_id: run id string
        
    Returns:
        conclusion or None
    """
    for _, repo_data in workflow_data.items():
        if 'runs' in repo_data:
            for run in repo_data['runs']:
                if str(run.get('id')) == run_id:
                    return run.get('conclusion')
    return None


def get_workflow_file_path(owner: str, repo: str) -> str:
    """
    Build workflow runs JSON path.
    
    Args:
        owner: repo owner
        repo: repo name
        
    Returns:
        file path
    """
    return f"results/workflow_runs/{owner}#{repo}#runs.json"


def analyze_download_logs():
    """
    Map extracted folders to run conclusions (one runs file per repo).
    """
    # paths
    unzipped_dir = "results/unzipped_workflow_detail_logs"
    # workflow_runs_dir = "results/workflow_runs"

    # 1. group run_ids by repo
    repo_to_runids = defaultdict(list)  # { (owner, repo): [run_id, ...] }
    folder_info = {}  # folder_name -> (owner, repo, repo_id, run_id)
    failed_parsing = []
    for item in os.listdir(unzipped_dir):
        item_path = os.path.join(unzipped_dir, item)
        if os.path.isdir(item_path):
            parsed = parse_folder_name(item)
            if not parsed:
                failed_parsing.append(item)
                continue
            owner, repo, repo_id, run_id = parsed
            repo_to_runids[(owner, repo)].append(run_id)
            folder_info[item] = (owner, repo, repo_id, run_id)

    print(f"Found {len(folder_info)} extracted folders across {len(repo_to_runids)} repos")

    # 2. per-repo lookup
    status_counts = Counter()
    repo_status_counts = defaultdict(Counter)
    missing_workflow_files = set()
    runid_status = {}  # (owner, repo, run_id) -> status
    repo_failed_runids = defaultdict(list)  # repo(str) -> [run_id,...]

    for (owner, repo), runid_list in repo_to_runids.items():
        workflow_file = get_workflow_file_path(owner, repo)
        if not os.path.exists(workflow_file):
            missing_workflow_files.add(f"{owner}#{repo}")
            for run_id in runid_list:
                runid_status[(owner, repo, run_id)] = None
            continue
        workflow_data = load_workflow_runs_file(workflow_file)
        # run_id -> conclusion
        runid_to_status = {}
        for _, repo_data in workflow_data.items():
            if 'runs' in repo_data:
                for run in repo_data['runs']:
                    runid_to_status[str(run.get('id'))] = run.get('conclusion')
        for run_id in runid_list:
            status = runid_to_status.get(str(run_id))
            runid_status[(owner, repo, run_id)] = status
            if status:
                status_counts[status] += 1
                repo_status_counts[f"{owner}/{repo}"][status] += 1
                if status == 'failure':
                    repo_failed_runids[f"{owner}/{repo}"].append(run_id)
            else:
                status_counts['not_found'] += 1
                repo_status_counts[f"{owner}/{repo}"]['not_found'] += 1

    # 3. report
    print("\n=== Overall ===")
    total_analyzed = sum(status_counts.values())
    print(f"Runs analyzed: {total_analyzed}")

    if total_analyzed > 0:
        print("\nStatus distribution:")
        for status, count in status_counts.most_common():
            percentage = (count / total_analyzed) * 100
            print(f"  {status}: {count} ({percentage:.2f}%)")

    # print(f"\n=== Per-repo stats ({len(repo_status_counts)} repos) ===")
    # for repo, counts in sorted(repo_status_counts.items()):
    #     total = sum(counts.values())
    #     success_rate = (counts.get('success', 0) / total) * 100 if total > 0 else 0
    #     print(f"{repo}: total {total}, success rate {success_rate:.2f}%")
    #     for status, count in counts.most_common():
    #         print(f"  - {status}: {count}")

    if failed_parsing:
        print(f"\n=== Folders with parse errors ({len(failed_parsing)}) ===")
        for folder in failed_parsing[:10]:
            print(f"  {folder}")
        if len(failed_parsing) > 10:
            print(f"  ... and {len(failed_parsing) - 10} more")

    if missing_workflow_files:
        print(f"\n=== Missing workflow runs files ({len(missing_workflow_files)}) ===")
        for repo in sorted(missing_workflow_files)[:10]:
            print(f"  {repo}")
        if len(missing_workflow_files) > 10:
            print(f"  ... and {len(missing_workflow_files) - 10} more")

    # save JSON report
    results = {
        "summary": {
            "total_successful_folders": len(folder_info),
            "total_analyzed": total_analyzed,
            "status_distribution": dict(status_counts),
            "failed_parsing_count": len(failed_parsing),
            "missing_workflow_files_count": len(missing_workflow_files)
        },
        "repo_details": {
            repo: {
                **dict(counts),
                "failed_run_ids": repo_failed_runids.get(repo, [])
            } for repo, counts in repo_status_counts.items()
        },
        "failed_parsing": failed_parsing,
        "missing_workflow_files": list(missing_workflow_files)
    }

    output_file = "results/download_logs_analysis.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nDetailed results saved to: {output_file}")


def main():

    print("Analyzing download logs...")
    analyze_download_logs()
    print("Done.")


if __name__ == "__main__":
    main()