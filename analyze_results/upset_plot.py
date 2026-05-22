# Run with: python -m analyze_results.upset_plot
import argparse
import os

import matplotlib.pyplot as plt
from upsetplot import UpSet, from_memberships
from venn import venn

from analyze_results.comparable_results import get_unified_comparable_results
from config import PROJECT_ROOT, RESULTS_DIR, results_fixed_docker_builds_dir


def get_success_cases(compare_result: dict[str, dict[str, str]]):
    """Extract per-tool success cases."""
    success_cases = {}
    for name, result in compare_result.items():
        if name == 'summary':
            continue
        success_cases[name] = {
            full_build_tag
            for full_build_tag, status in result.items()
            if full_build_tag not in ('common_success', 'summary') and status == 'success'
        }
    return success_cases


def get_domains(compare_result: dict[str, dict[str, str]], compare_tool: str = 'DoFix-V3'):
    success_sets_all = {
        name: {tag for tag, st in res.items() if tag != 'summary' and st == 'success'}
        for name, res in compare_result.items()
    }
    dofix_domain = {
        tag for tag, st in compare_result[compare_tool].items()
        if tag != 'summary' and st != 'skipped'
    }
    success_sets_in_dofix = {name: (tags & dofix_domain) for name, tags in success_sets_all.items()}
    return success_sets_all, success_sets_in_dofix


def get_venn_plot(compare_result, compare_tool: str, plots_dir: str):
    _, success_sets_in_dofix = get_domains(compare_result, compare_tool)
    plt.figure(figsize=(12, 10))
    venn(success_sets_in_dofix)
    plt.title(f"Success Venn within {compare_tool} domain")
    plt.tight_layout()
    output_path = os.path.join(plots_dir, f"venn_success_in_{compare_tool}_domain.pdf")
    plt.savefig(output_path, dpi=600)
    plt.show()
    print(f"Saved Venn plot to {output_path}")


def get_upset_plot(compare_result, compare_tool: str, plots_dir: str):
    _, success_sets_in_dofix = get_domains(compare_result, compare_tool)
    all_tags = set().union(*success_sets_in_dofix.values())
    memberships = [
        [name for name, s in success_sets_in_dofix.items() if tag in s]
        for tag in all_tags
    ]
    data = from_memberships(memberships)
    plt.figure(figsize=(12, 8))
    up = UpSet(
        data,
        subset_size='count',
        sort_by='cardinality',
        sort_categories_by='cardinality',
        show_counts=True,
    )
    up.style_categories(compare_tool, shading_facecolor="lavender")
    up.plot()
    output_path = os.path.join(plots_dir, f"upset_plot_success_in_{compare_tool}_domain.pdf")
    plt.savefig(output_path, dpi=600)
    plt.show()
    print(f"Saved UpSet plot to {output_path}")


def _default_result_paths() -> dict[str, str]:
    """Default tool-name -> run_logs directory map, resolved from config.RESULTS_DIR.

    Adjust by passing --result_paths name1=path1 name2=path2 ... on the CLI if
    your local layout differs (e.g. no ablation subdirectory).
    """
    ablation = RESULTS_DIR / "ablation"
    r = results_fixed_docker_builds_dir  # shorthand
    return {
        "FlakiDock-GPT4": str(ablation / "fixed_docker_builds_flakidock_gpt_4_sv11/run_logs"),
        "FlakiDock-V3": str(ablation / "fixed_docker_builds_flakidock_upgrade_response_deal_DeepSeek-V3/run_logs"),
        "Parfum": str(r("parfum", "standard", "DeepSeek-V3") / "run_logs"),
        "Pure-LLM-V3": str(r("pure-llm", "standard", "DeepSeek-V3") / "run_logs"),
        "DoFix-V3": str(r("dofix", "standard", "DeepSeek-V3") / "run_logs"),
    }


def _parse_result_paths(pairs: list[str] | None) -> dict[str, str] | None:
    if not pairs:
        return None
    parsed: dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise SystemExit(f"--result_paths entries must be name=path, got: {item}")
        name, _, path = item.partition("=")
        parsed[name.strip()] = path.strip()
    return parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Venn / UpSet plots of per-tool repair success sets.")
    parser.add_argument("--result_paths", nargs="+", default=None, metavar="NAME=PATH", help="Override name=run_logs directory mapping; omit to use built-in defaults.")
    parser.add_argument("--compare_tool", default="DoFix-V3", help="Restrict plots to the attempt domain of this tool.")
    parser.add_argument("--plots_dir", default=str(PROJECT_ROOT / "plots"), help="PDF output directory.")
    return parser.parse_args()


def main():
    args = _parse_args()
    result_paths = _parse_result_paths(args.result_paths) or _default_result_paths()
    os.makedirs(args.plots_dir, exist_ok=True)

    results, compare_result = get_unified_comparable_results(result_paths, compare_tool=args.compare_tool)
    for tool, result in results.items():
        print(f"{tool}: {result['summary']}")
    for tool, result in compare_result.items():
        print(f"{tool}: {result['summary']}")

    get_venn_plot(compare_result, compare_tool=args.compare_tool, plots_dir=args.plots_dir)
    get_upset_plot(compare_result, compare_tool=args.compare_tool, plots_dir=args.plots_dir)


if __name__ == '__main__':
    main()
