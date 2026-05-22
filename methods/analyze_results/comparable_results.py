# Run: cd project root && source .venv/bin/activate && python -m methods.analyze_results.comparable_results
"""Summarize build results per repair method on a unified comparable build set."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import PROJECT_ROOT


def resolve_repo_path(rel: str | Path) -> Path:
    """Resolve a path relative to the project root to an absolute path."""
    p = Path(rel)
    return p if p.is_absolute() else PROJECT_ROOT / p


def get_unified_comparable_results(result_paths: dict[str, str | Path], compare_tool: str = "dofix_v3"):
    results = {}
    status = set()
    skip_reasons = set()
    for name, rel_path in result_paths.items():
        path = resolve_repo_path(rel_path)
        if not path.is_dir():
            print(f"WARN: skip {name}: directory not found: {path.relative_to(PROJECT_ROOT)}")
            continue
        results[name] = {}
        for file in os.listdir(path):
            with open(path / file, encoding="utf-8") as f:
                data = json.load(f)
                for repair_info in data.get("builds", []):
                    results[name][repair_info["full_build_tag"].replace(f'#{repair_info["fix_method"]}', "")] = repair_info["status"]
                    status.add(repair_info["status"])
                    if repair_info["status"] == "skipped":
                        skip_reasons.add(repair_info.get("error", ""))
    # print(status)
    # print(list(skip_reasons), '\n\n')
    for name, result in results.items():
        results[name]["summary"] = {
            "success": sum(1 for status in result.values() if status == "success"),
            "failed": sum(1 for status in result.values() if status == "failed"),
            "skipped": sum(1 for status in result.values() if status == "skipped"),
            "error": sum(1 for status in result.values() if status == "error"),
            "timeout": sum(1 for status in result.values() if status == "timeout"),
            "total": sum(1 for id in result.keys() if id != "summary"),
        }
        success_rate = results[name]["summary"]["success"] / (
            results[name]["summary"]["total"] - results[name]["summary"]["skipped"]
        )
        results[name]["summary"]["success_rate"] = success_rate
        # print(name, results[name]['summary'])

    # print('\n\n')

    if compare_tool not in results:
        loaded = ", ".join(results.keys()) or "(none)"
        raise KeyError(
            f"compare_tool {compare_tool!r} not in loaded results. "
            f"Available: {loaded}"
        )

    compare_list = {}
    for full_build_tag, status in results[compare_tool].items():
        if status != "skipped":
            compare_list[full_build_tag] = status

    compare_result = {}

    for name, result in results.items():
        for full_build_tag, status in result.items():
            if full_build_tag in compare_list.keys():
                if name not in compare_result:
                    compare_result[name] = {}
                compare_result[name][full_build_tag] = status
        for full_build_tag in compare_list.keys():
            if full_build_tag not in result:
                compare_result[name][full_build_tag] = "missed"

    for name, _ in compare_result.items():
        compare_result[name]["common_success"] = []

    for name, result in compare_result.items():
        for full_build_tag, status in result.items():
            if (
                full_build_tag != "common_success"
                and full_build_tag in compare_list
                and compare_list[full_build_tag] == "success"
                and status == "success"
            ):
                compare_result[name]["common_success"].append(full_build_tag)

    for _name, result in compare_result.items():
        result["summary"] = {
            "success": sum(1 for status in result.values() if status == "success"),
            "failed": sum(1 for status in result.values() if status == "failed"),
            "skipped": sum(1 for status in result.values() if status == "skipped"),
            "error": sum(1 for status in result.values() if status == "error"),
            "timeout": sum(1 for status in result.values() if status == "timeout"),
            "missed": sum(1 for status in result.values() if status == "missed"),
            "rebuilding": sum(1 for status in result.values() if status == "rebuilding"),
            "total": sum(1 for id in result.keys() if id != "summary" and id != "common_success"),
        }
        result["summary"]["success_rate"] = result["summary"]["success"] / (result["summary"]["total"])  #  - result['summary']['skipped'])
        # print(name, result['summary'])
        # print(name, len(result['common_success']))

    return results, compare_result


if __name__ == "__main__":
    flakidock_deal_v3 = "results/ablation/fixed_docker_builds_flakidock_upgrade_response_deal_DeepSeek-V3/run_logs"
    # flakidock_ori_gpt_4_svdo = "results/ablation/fixed_docker_builds_flakidock_gpt_4_svdo/run_logs"
    flakidock_ori_gpt_4_sv11 = "results/ablation/fixed_docker_builds_flakidock_gpt_4_sv11/run_logs"
    flakidock_ori_v3 = "results/ablation/fixed_docker_builds_flakidock_DeepSeek-V3/run_logs"
    parfum = "results/ablation/fixed_docker_builds_parfum/run_logs"
    dofix_v3 = "results/fixed_docker_builds/dofix/standard/DeepSeek-V3.2509/run_logs"
    # dofix_v3 = "results/fixed_docker_builds_one_dir_one_file_DeepSeek-V3/run_logs"
    # dofix_gpt_5 = "results/ablation/fixed_docker_builds_one_dir_one_file/run_logs"
    pure_llm_v3 = "results/ablation/fixed_docker_builds_pure_llm_DeepSeek-V3/run_logs"
    remove_key_files_v3 = "results/fixed_docker_builds/dofix/remove_key_files/DeepSeek-V3/run_logs"
    remove_build_channel_v3 = (
        "results/ablation/fixed_docker_builds_dofix_remove_build_channel_DeepSeek-V3/run_logs"
    )

    result_paths = {
        # "flakidock_ori_gpt_4_svdo": flakidock_ori_gpt_4_svdo,
        "FlakiDock-GPT4": flakidock_ori_gpt_4_sv11,
        "FlakiDock-V3": flakidock_deal_v3,
        "flakidock_ori_v3": flakidock_ori_v3,
        "Parfum": parfum,
        "Pure-LLM-V3": pure_llm_v3,
        "DoFix-V3": dofix_v3,
        "DoFix-V3-Remove-Key-Files": remove_key_files_v3,
        "DoFix-V3-Remove-Build-Channel": remove_build_channel_v3,
        # "dofix_gpt_5": dofix_gpt_5
    }
    results, compare_result = get_unified_comparable_results(result_paths, compare_tool="DoFix-V3")
    for tool, result in results.items():
        print(f"{tool}: {result['summary']}")
    print("\n\n")
    for tool, result in compare_result.items():
        print(f"{tool}: {result['summary']}")
