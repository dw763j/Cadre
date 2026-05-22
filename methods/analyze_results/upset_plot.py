import matplotlib.pyplot as plt
from venn import venn
from comparable_results import get_unified_comparable_results
from upsetplot import UpSet, from_memberships
import os

def get_success_cases(compare_result: dict[str, dict[str, str]]):
    # Extract successful cases per tool
    success_cases = {}
    for name, result in compare_result.items():
        if name != 'summary':  # exclude summary key
            success_cases[name] = set()
            for full_build_tag, status in result.items():
                if full_build_tag != 'common_success' and full_build_tag != 'summary' and status == 'success':
                    success_cases[name].add(full_build_tag)
    return success_cases

def get_domains(compare_result: dict[str, dict[str, str]], compare_tool: str = 'DoFix-V3'):
    success_sets_all = {}
    for name, res in compare_result.items():
        success_sets_all[name] = {tag for tag, st in res.items() if tag != 'summary' and st == 'success'}
    dofix_domain = {tag for tag, st in compare_result[compare_tool].items() if tag != 'summary' and st != 'skipped'}
    success_sets_in_dofix = {name: (tags & dofix_domain) for name, tags in success_sets_all.items()}
    return success_sets_all, success_sets_in_dofix

def get_venn_plot(
    compare_result: dict[str, dict[str, str]],
    compare_tool: str = 'DoFix-V3',
    out_path: str = 'plots/venn_success_in_DoFix_domain.pdf',
    fontsize: int = 14,
):
    success_sets_all, success_sets_in_dofix = get_domains(compare_result, compare_tool)
    plt.figure(figsize=(8, 6))
    venn(success_sets_in_dofix)
    # Enlarge title and all in-figure text
    plt.title(f"Success Venn within {compare_tool} domain", fontsize=fontsize + 2)
    ax = plt.gca()
    for text in ax.texts:
        try:
            text.set_fontsize(fontsize)
        except Exception:
            pass
    plt.tight_layout()
    # Save as PDF
    plt.savefig(out_path, dpi=600)
    plt.show()

def get_upset_plot(compare_result: dict[str, dict[str, str]], compare_tool: str = 'DoFix-V3', out_path: str = 'plots/upset_plot_success_in_DoFix_domain.pdf'):
    success_sets_all, success_sets_in_dofix = get_domains(compare_result, compare_tool)
    all_tags = set().union(*success_sets_in_dofix.values())
    memberships = []
    for tag in all_tags:
        present_in = [name for name, s in success_sets_in_dofix.items() if tag in s]
        memberships.append(present_in)
    data = from_memberships(memberships)
    plt.figure(figsize=(10, 10))
    up = UpSet(
        data,
        subset_size='count', # avoid non-unique index errors from subset_size="auto"
        sort_by='cardinality', # sort intersections by size
        sort_categories_by='cardinality', # category order follows input
        show_counts=True
    )
    up.style_categories(
        compare_tool,
        shading_facecolor="lavender",
    )
    up.plot()
    plt.savefig(out_path, dpi=600)
    plt.show()

if __name__ == '__main__':
    flakidock_deal_v3 = "results/ablation/fixed_docker_builds_flakidock_upgrade_response_deal_DeepSeek-V3/run_logs"
    # flakidock_ori_gpt_4_svdo = "results/ablation/fixed_docker_builds_flakidock_gpt_4_svdo/run_logs"
    flakidock_ori_gpt_4_sv11 = "results/ablation/fixed_docker_builds_flakidock_gpt_4_sv11/run_logs"
    # flakidock_ori_v3 = "results/ablation/fixed_docker_builds_flakidock_DeepSeek-V3/run_logs"
    parfum = "results/ablation/fixed_docker_builds_parfum/run_logs"
    dofix_v3 = "results/fixed_docker_builds_one_dir_one_file_DeepSeek-V3/run_logs"
    # dofix_gpt_5 = "results/ablation/fixed_docker_builds_one_dir_one_file/run_logs"
    pure_llm_v3 = "results/ablation/fixed_docker_builds_pure_llm_DeepSeek-V3/run_logs"
    remove_build_channel_v3 = 'results/ablation/fixed_docker_builds_dofix_remove_build_channel_DeepSeek-V3/run_logs'
    remove_key_files_v3 = "results/fixed_docker_builds_dofix_remove_key_files_DeepSeek-V3/run_logs"
    result_paths = {
        # "flakidock_ori_gpt_4_svdo": flakidock_ori_gpt_4_svdo,
        "FlakiDock-GPT4": flakidock_ori_gpt_4_sv11,
        "FlakiDock-DS-V3": flakidock_deal_v3,
        # "flakidock_ori_v3": flakidock_ori_v3,
        "Parfum": parfum,
        "Vanilla-LLM": pure_llm_v3,
        "Cadre": dofix_v3,
        # "Cadre-CDG": remove_build_channel_v3,
        # "Cadre-CRR": remove_key_files_v3,
        # "dofix_gpt_5": dofix_gpt_5
    }
    results, compare_result = get_unified_comparable_results(result_paths, compare_tool='Cadre')
    for tool, result in results.items():
        print(f"{tool}: {result['summary']}")
    for tool, result in compare_result.items():
        print(f"{tool}: {result['summary']}")
    os.makedirs('plots', exist_ok=True)
    get_venn_plot(compare_result, compare_tool='Cadre', out_path='plots/venn_success_in_Cadre_domain.pdf', fontsize=16)
    get_upset_plot(compare_result, compare_tool='Cadre', out_path='plots/upset_plot_success_in_Cadre_domain.pdf')