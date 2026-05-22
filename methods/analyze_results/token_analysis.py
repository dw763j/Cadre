import json
import os
import tiktoken
import numpy as np
import matplotlib.pyplot as plt
from comparable_results import get_unified_comparable_results, resolve_repo_path

MAX_TOKENS_DS_V3 = 65535


def num_gpt_tokens_in_a_string(string: str, encoding_name: str = 'gpt2') -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

def get_all_tokens():
    llm_token_path = resolve_repo_path('results/ablation/repairing_dockerfile_pure-llm_DeepSeek-V3_tokens/repair_results_pure-llm')
    dofix_token_path = resolve_repo_path('results/repairing_dockerfile_one_dir_one_file_DeepSeek-V3/repair_results_dofix')
    flakidock_token_path = resolve_repo_path(
        'results/ablation/repairing_dockerfile_do/'
        'flakidock_upgrade_response_deal_DeepSeek-V3/generated_prompts'
    )
    all_tokens = {
        'llm_tokens':{},
        'dofix_tokens':{},
        'flakidock_tokens':{}
    }

    for file in os.listdir(llm_token_path):
        with open(llm_token_path / file, encoding='utf-8') as f:
            data = json.load(f)
            for _id, repair_info in data.items():
                for iid, rr in repair_info.items():
                    all_tokens['llm_tokens'][iid.replace('#pure-llm#DeepSeek-V3', '')] = rr.get('input_num_tokens', -1)

    for file in os.listdir(dofix_token_path):
        with open(dofix_token_path / file, encoding='utf-8') as f:
            data = json.load(f)
            for _id, repair_info in data.items():
                for iid, rr in repair_info.items():
                    all_tokens['dofix_tokens'][iid.replace('#dofix#DeepSeek-V3', '')] = rr.get('usage', {}).get('fix', {}).get('prompt_tokens', -1)

    for file in os.listdir(flakidock_token_path):
        if file.endswith('.json'):
            with open(flakidock_token_path / file, encoding='utf-8') as f:
                data = json.load(f)
                iid = file.replace('#flakidock_original_results.json', '')
                all_tokens['flakidock_tokens'][iid] = data.get('chat_feedback', {}).get('prompt_tokens', -1)
                if all_tokens['flakidock_tokens'][iid] == -1:
                    print(iid)
        if file.endswith('.txt'):
            with open(flakidock_token_path / file, encoding='utf-8') as f:
                data = f.read()
                iid = file.replace('#flakidock.txt', '')
                try:
                    all_tokens['flakidock_tokens'][iid] = num_gpt_tokens_in_a_string(data)
                except Exception:
                    print(iid)
                    all_tokens['flakidock_tokens'][iid] = -1

    print(sum(all_tokens['dofix_tokens'].values()) / len(all_tokens['dofix_tokens']))
    print(sum(all_tokens['llm_tokens'].values()) / len(all_tokens['llm_tokens']))
    print(sum(all_tokens['flakidock_tokens'].values()) / len(all_tokens['flakidock_tokens']))

    print(len(all_tokens['dofix_tokens']))
    print(len(all_tokens['llm_tokens']))
    print(len(all_tokens['flakidock_tokens']))

    flakidock_deal_v3 = "results/ablation/fixed_docker_builds_flakidock_upgrade_response_deal_DeepSeek-V3/run_logs"
    # flakidock_ori_gpt_4_svdo = "results/ablation/fixed_docker_builds_flakidock_gpt_4_svdo/run_logs"
    # flakidock_ori_gpt_4_sv11 = "results/ablation/fixed_docker_builds_flakidock_gpt_4_sv11/run_logs"
    # flakidock_ori_v3 = "results/ablation/fixed_docker_builds_flakidock_DeepSeek-V3/run_logs"
    # parfum = "results/ablation/fixed_docker_builds_parfum/run_logs"
    dofix_v3 = "results/fixed_docker_builds_one_dir_one_file_DeepSeek-V3/run_logs"
    # dofix_gpt_5 = "results/ablation/fixed_docker_builds_one_dir_one_file/run_logs"
    pure_llm_v3 = "results/ablation/fixed_docker_builds_pure_llm_DeepSeek-V3/run_logs"

    result_paths = {
        # "flakidock_ori_gpt_4_svdo": flakidock_ori_gpt_4_svdo,
        # "FlakiDock-GPT4": flakidock_ori_gpt_4_sv11,
        "FlakiDock-DS-V3": flakidock_deal_v3,
        # "flakidock_ori_v3": flakidock_ori_v3,
        # "Parfum": parfum,
        "Vanilla-LLM": pure_llm_v3,
        "Cadre": dofix_v3,
        # "dofix_gpt_5": dofix_gpt_5
    }
    _results, compare_result = get_unified_comparable_results(result_paths, compare_tool='Cadre')
    for tool, result in compare_result.items():
        if tool == 'Cadre':
            for k, v in result.items():
                if k != 'common_success' and k != 'summary':
                    result[k] = {
                        'total_tokens': all_tokens['dofix_tokens'][k] if k in all_tokens['dofix_tokens'] else -1,
                        'status': v
                    }
        elif tool == 'Vanilla-LLM':
            for k, v in result.items():
                if k != 'common_success' and k != 'summary':
                    result[k] = {
                        'total_tokens': all_tokens['llm_tokens'][k] if k in all_tokens['llm_tokens'] else -1,
                        'status': v
                    }
        elif tool == 'FlakiDock-DS-V3':
            for k, v in result.items():
                if k != 'common_success' and k != 'summary':
                    result[k] = {
                        'total_tokens': all_tokens['flakidock_tokens'][k] if k in all_tokens['flakidock_tokens'] else -1,
                        'status': v
                    }

    with open('token_analysis.json', 'w') as f:
        json.dump(compare_result, f, indent=2)

get_all_tokens()

# =====================
# Plotting
# =====================

os.makedirs('plots', exist_ok=True)

def collect_tool_records(compare_result: dict) -> dict:
    tool_to_records = {}
    for tool_name, result in compare_result.items():
        if tool_name in ('summary',):
            continue
        records = []
        for tag, info in result.items():
            if tag in ('summary', 'common_success'):
                continue
            if isinstance(info, dict):
                tokens = info.get('total_tokens', -1)
                status = info.get('status', '')
            else:
                # Fallback if structure changes
                tokens = -1
                status = str(info)
            if tokens is None:
                tokens = -1
            if tokens == -1:
                continue
            if status in ('missed', 'skipped'):
                continue
            # Enforce DeepSeek-V3 token cap: any tokens over cap -> non-success; clip for plotting
            over_cap = tokens > MAX_TOKENS_DS_V3
            tokens = min(tokens, MAX_TOKENS_DS_V3)
            is_success = 0 if over_cap else (1 if status == 'success' else 0)
            records.append((tokens, is_success))
        tool_to_records[tool_name] = records
    return tool_to_records

def plot_box_success(tokens_and_success: list[tuple[int, int]], title: str, out_path: str):
    if not tokens_and_success:
        return
    success_tokens = [t for t, s in tokens_and_success if s == 1]
    nonsuccess_tokens = [t for t, s in tokens_and_success if s == 0]
    if len(success_tokens) == 0 and len(nonsuccess_tokens) == 0:
        return
    plt.figure(figsize=(6, 4))
    data = []
    labels = []
    if len(success_tokens) > 0:
        data.append(success_tokens)
        labels.append('Success')
    if len(nonsuccess_tokens) > 0:
        data.append(nonsuccess_tokens)
        labels.append('Non-success')
    plt.boxplot(data, labels=labels, showfliers=False)
    plt.ylabel('Prompt tokens')
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()

def plot_binned_success_rate(tokens_and_success: list[tuple[int, int]], title: str, out_path: str, num_bins: int = 10):
    if not tokens_and_success:
        return
    tokens = np.array([t for t, _ in tokens_and_success], dtype=float)
    success = np.array([s for _, s in tokens_and_success], dtype=int)
    if len(tokens) < 2:
        return
    # Define bins across range; fall back to unique counts if degenerate
    t_min, t_max = float(np.min(tokens)), float(np.max(tokens))
    if t_max == t_min:
        return
    bin_edges = np.linspace(t_min, t_max, num_bins + 1)
    bin_indices = np.digitize(tokens, bin_edges, right=False) - 1
    # Clamp last bin index
    bin_indices = np.clip(bin_indices, 0, num_bins - 1)

    bin_success_rate = []
    bin_centers = []
    for b in range(num_bins):
        mask = bin_indices == b
        count = int(np.sum(mask))
        if count == 0:
            continue
        succ = int(np.sum(success[mask]))
        rate = succ / count
        left, right = bin_edges[b], bin_edges[b + 1]
        center = 0.5 * (left + right)
        bin_centers.append(center)
        bin_success_rate.append(rate)

    if len(bin_centers) == 0:
        return

    plt.figure(figsize=(6, 4))
    plt.plot(bin_centers, bin_success_rate, marker='o')
    plt.xlabel('Prompt tokens (binned)')
    plt.ylabel('Success rate')
    plt.ylim(0, 1)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()

# Ensure fresh token aggregation and mapping
# get_all_tokens()
with open('token_analysis.json') as f:
    compare_result = json.load(f)
tool_records = collect_tool_records(compare_result)

def plot_aggregate_grouped_box(tool_records: dict):
    tools = []
    success_lists = []
    nonsuccess_lists = []
    for tool_name, records in tool_records.items():
        success_tokens = [t for t, s in records if s == 1]
        nonsuccess_tokens = [t for t, s in records if s == 0]
        if len(success_tokens) == 0 and len(nonsuccess_tokens) == 0:
            continue
        tools.append(tool_name)
        success_lists.append(success_tokens)
        nonsuccess_lists.append(nonsuccess_tokens)
    if len(tools) == 0:
        return
    plt.figure(figsize=(max(6, 1.4 * len(tools)), 5))
    group_centers = np.arange(len(tools))
    box_width = 0.35
    offset = box_width * 0.7
    pos_success = group_centers - offset
    pos_non = group_centers + offset
    # Success boxes
    bp_s = plt.boxplot(
        success_lists,
        positions=pos_success,
        widths=box_width,
        showfliers=False,
        patch_artist=True
    )
    for patch in bp_s['boxes']:
        patch.set_facecolor('#7fbf7f')
    # Non-success boxes
    bp_n = plt.boxplot(
        nonsuccess_lists,
        positions=pos_non,
        widths=box_width,
        showfliers=False,
        patch_artist=True
    )
    for patch in bp_n['boxes']:
        patch.set_facecolor('#fdbf6f')
    plt.xticks(group_centers, tools, rotation=20, ha='right')
    plt.ylabel('Prompt tokens')
    plt.title('Tokens vs Success grouped by tools')
    # Legend
    from matplotlib.patches import Patch
    plt.legend(
        handles=[Patch(facecolor='#7fbf7f', label='Success'), Patch(facecolor='#fdbf6f', label='Non-success')],
        loc='best'
    )
    plt.tight_layout()
    plt.savefig(os.path.join('plots', 'tokens_vs_success_grouped_all_tools.pdf'), dpi=300)
    plt.close()

def plot_aggregate_multiline_binned(tool_records: dict, num_bins: int = 10):
    # collect global min/max
    all_tokens = []
    for _tool, records in tool_records.items():
        all_tokens.extend([t for t, _ in records])
    if len(all_tokens) < 2:
        return
    t_min = float(np.min(all_tokens))
    t_max = float(np.max(all_tokens))
    if t_max == t_min:
        return
    bin_edges = np.linspace(t_min, t_max, num_bins + 1)
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    plt.figure(figsize=(8, 5))
    for tool_name, records in tool_records.items():
        if len(records) == 0:
            continue
        tokens = np.array([t for t, _ in records], dtype=float)
        success = np.array([s for _, s in records], dtype=int)
        if len(tokens) == 0:
            continue
        bin_indices = np.digitize(tokens, bin_edges, right=False) - 1
        bin_indices = np.clip(bin_indices, 0, num_bins - 1)
        rates = []
        centers = []
        for b in range(num_bins):
            mask = bin_indices == b
            count = int(np.sum(mask))
            if count == 0:
                rates.append(np.nan)
                centers.append(bin_centers[b])
                continue
            succ = int(np.sum(success[mask]))
            rates.append(succ / count)
            centers.append(bin_centers[b])
        # mask out bins with no data
        rates = np.array(rates, dtype=float)
        centers = np.array(centers, dtype=float)
        valid = ~np.isnan(rates)
        if np.any(valid):
            plt.plot(centers[valid], rates[valid], marker='o', label=tool_name)
    plt.xlabel('Prompt tokens (binned)')
    plt.ylabel('Success rate')
    plt.ylim(0, 1)
    plt.title('Success rate across token bins (all tools)')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join('plots', 'tokens_success_rate_bins_all_tools.pdf'), dpi=300)
    plt.close()
for tool_name, records in tool_records.items():
    safe_tool = tool_name.replace('/', '_')
    plot_box_success(
        records,
        title=f"{tool_name}: Tokens vs Success",
        out_path=os.path.join('plots', f"tokens_vs_success_box_{safe_tool}.pdf"),
    )
    plot_binned_success_rate(
        records,
        title=f"{tool_name}: Success Rate across Token Bins",
        out_path=os.path.join('plots', f"tokens_success_rate_bins_{safe_tool}.pdf"),
        num_bins=10,
    )

# Aggregate plots
plot_aggregate_grouped_box(tool_records)
plot_aggregate_multiline_binned(tool_records, num_bins=10)

def get_tool_tokens_all(compare_result: dict, clip_to_cap: bool = False) -> dict[str, list[int]]:
    tool_to_tokens: dict[str, list[int]] = {}
    for tool_name, result in compare_result.items():
        if tool_name in ('summary',):
            continue
        tokens_list: list[int] = []
        for tag, info in result.items():
            if tag in ('summary', 'common_success'):
                continue
            if isinstance(info, dict):
                tokens = info.get('total_tokens', -1)
            else:
                tokens = -1
            if tokens is None or tokens == -1:
                continue
            if clip_to_cap:
                tokens = min(int(tokens), MAX_TOKENS_DS_V3)
            else:
                tokens = int(tokens)
            tokens_list.append(tokens)
        tool_to_tokens[tool_name] = tokens_list
    return tool_to_tokens

def plot_token_usage_histogram_all_tools(compare_result: dict, bin_size: int = 10000, clip_to_cap: bool = False):
    tool_to_tokens = get_tool_tokens_all(compare_result, clip_to_cap=clip_to_cap)
    # Determine dynamic max token across all tools
    all_tokens = [t for tokens in tool_to_tokens.values() for t in tokens]
    if len(all_tokens) == 0:
        return
    max_token = max(all_tokens)
    # Build bin edges with a single overflow bin for > 65535:
    # [0, 10000, 20000, 30000, 40000, 50000, 60000, 65536, (max_token+1 if overflow exists)]
    bin_edges = list(range(0, 60000 + 1, bin_size))
    if bin_edges[-1] != 60000:
        # Ensure 60000 is included as the last regular lower bound
        bin_edges = [0, 10000, 20000, 30000, 40000, 50000, 60000]
    bin_edges.append(65536)
    has_overflow = max_token > MAX_TOKENS_DS_V3
    if has_overflow:
        bin_edges.append(int(max_token) + 1)
    # Labels
    labels = []
    for i in range(len(bin_edges) - 1):
        left, right = bin_edges[i], bin_edges[i + 1]
        if has_overflow and i == len(bin_edges) - 2:
            labels.append("65k+")
        else:
            left_label = '0' if left == 0 else f"{left // 1000}k"
            right_label = f"{right // 1000}k"
            labels.append(f"{left_label}-{right_label}")

    # Compute counts per tool
    tool_names = list(tool_to_tokens.keys())
    counts_per_tool = []
    for tool in tool_names:
        tokens = np.array(tool_to_tokens[tool], dtype=int)
        if tokens.size == 0:
            counts = np.zeros(len(labels), dtype=int)
        else:
            counts, _ = np.histogram(tokens, bins=bin_edges)
        counts_per_tool.append(counts)

    # Plot grouped bars
    if len(tool_names) == 0:
        return
    num_bins = len(labels)
    x = np.arange(num_bins)
    num_tools = len(tool_names)
    total_width = min(0.8, 0.2 * num_tools)
    bar_width = total_width / max(1, num_tools)
    start = -total_width / 2

    plt.figure(figsize=(max(8, 1.2 * num_bins), 5))
    for idx, tool in enumerate(tool_names):
        offsets = x + start + idx * bar_width
        bars = plt.bar(offsets, counts_per_tool[idx], width=bar_width, label=tool)
        # annotate values on top of bars
        for rect, value in zip(bars, counts_per_tool[idx], strict=False):
            height = rect.get_height()
            if value == 0:
                continue
            plt.text(
                rect.get_x() + rect.get_width() / 2.0,
                height,
                str(int(value)),
                ha='center',
                va='bottom',
                fontsize=8
            )
    plt.xticks(x, labels, rotation=30, ha='right')
    plt.xlabel('Prompt tokens (bins)')
    plt.ylabel('Count of results')
    plt.title('Token usage distribution across results')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join('plots', 'token_usage_hist_all_tools_10k_bins.pdf'), dpi=300)
    plt.close()

plot_token_usage_histogram_all_tools(compare_result, bin_size=10000, clip_to_cap=False)

def plot_token_usage_histogram_all_tools_broken(compare_result: dict, bin_size: int = 10000, clip_to_cap: bool = False,
                                               break_point: int | None = None, top_ratio: float = 0.35):
    """Draw grouped histogram with a broken y-axis to improve readability when a few bins dominate.

    break_point: if None, choose automatically at the 90th percentile of non-zero bin counts.
    top_ratio: relative height of the top subplot.
    """
    tool_to_tokens = get_tool_tokens_all(compare_result, clip_to_cap=clip_to_cap)
    all_tokens = [t for tokens in tool_to_tokens.values() for t in tokens]
    if len(all_tokens) == 0:
        return
    max_token = max(all_tokens)
    bin_edges = list(range(0, 60000 + 1, bin_size))
    if bin_edges[-1] != 60000:
        bin_edges = [0, 10000, 20000, 30000, 40000, 50000, 60000]
    bin_edges.append(65536)
    has_overflow = max_token > MAX_TOKENS_DS_V3
    if has_overflow:
        bin_edges.append(int(max_token) + 1)

    labels = []
    for i in range(len(bin_edges) - 1):
        left, right = bin_edges[i], bin_edges[i + 1]
        if has_overflow and i == len(bin_edges) - 2:
            labels.append("65k+")
        else:
            left_label = '0' if left == 0 else f"{left // 1000}k"
            right_label = f"{right // 1000}k"
            labels.append(f"{left_label}-{right_label}")

    tool_names = list(tool_to_tokens.keys())
    if len(tool_names) == 0:
        return

    counts_per_tool = []
    for tool in tool_names:
        tokens = np.array(tool_to_tokens[tool], dtype=int)
        if tokens.size == 0:
            counts = np.zeros(len(labels), dtype=int)
        else:
            counts, _ = np.histogram(tokens, bins=bin_edges)
        counts_per_tool.append(counts)

    # Determine breaking point
    stacked_counts = np.sum(np.stack(counts_per_tool, axis=0), axis=0)
    non_zero = stacked_counts[stacked_counts > 0]
    if non_zero.size == 0:
        return
    if break_point is None:
        # Choose a threshold between typical bars and outliers (slightly more aggressive)
        threshold = int(np.percentile(non_zero, 80))
    else:
        threshold = int(break_point)

    num_bins = len(labels)
    x = np.arange(num_bins)
    num_tools = len(tool_names)
    total_width = min(0.8, 0.2 * num_tools)
    bar_width = total_width / max(1, num_tools)
    start = -total_width / 2

    # Create broken axis
    height = 6
    fig, (ax_top, ax_bottom) = plt.subplots(2, 1, sharex=True,
                                            gridspec_kw={'height_ratios': [top_ratio, 1 - top_ratio]},
                                            figsize=(max(8, 1.2 * num_bins), height))

    # Draw bars on both axes
    for idx, tool in enumerate(tool_names):
        offsets = x + start + idx * bar_width
        bars_top = ax_top.bar(offsets, counts_per_tool[idx], width=bar_width, label=tool)
        bars_bottom = ax_bottom.bar(offsets, counts_per_tool[idx], width=bar_width)
        for rect, value in zip(bars_top, counts_per_tool[idx], strict=False):
            if value == 0:
                continue
            ax_top.text(rect.get_x() + rect.get_width() / 2.0, rect.get_height(), str(int(value)),
                        ha='center', va='bottom', fontsize=8)
        for rect, value in zip(bars_bottom, counts_per_tool[idx], strict=False):
            if value == 0:
                continue
            # annotate only if it will be visible below threshold
            if rect.get_height() <= threshold:
                ax_bottom.text(rect.get_x() + rect.get_width() / 2.0, rect.get_height(), str(int(value)),
                                ha='center', va='bottom', fontsize=8)

    # Set y-limits
    ymax = int(np.max(stacked_counts))
    ax_bottom.set_ylim(0, threshold)
    ax_top.set_ylim(max(threshold + 1, threshold * 1.02), ymax * 1.05)

    # Diagonal break marks
    d = .5
    kwargs = dict(transform=ax_top.transAxes, color='k', clip_on=False)
    ax_top.plot((-d, +d), (-0.02, +0.02), **kwargs)
    ax_top.plot((1 - d, 1 + d), (-0.02, +0.02), **kwargs)
    kwargs.update(transform=ax_bottom.transAxes)
    ax_bottom.plot((-d, +d), (1 - 0.02, 1 + 0.02), **kwargs)
    ax_bottom.plot((1 - d, 1 + d), (1 - 0.02, 1 + 0.02), **kwargs)

    ax_bottom.set_xticks(x)
    ax_bottom.set_xticklabels(labels, rotation=30, ha='right')
    fig.suptitle('Token usage distribution across results (broken y-axis)')
    ax_bottom.set_xlabel('Prompt tokens (bins)')
    ax_top.set_ylabel('Count of results')
    ax_bottom.set_ylabel('Count of results')
    ax_top.legend(loc='upper right')
    fig.tight_layout()
    fig.subplots_adjust(hspace=0.05)
    fig.savefig(os.path.join('plots', 'token_usage_hist_all_tools_10k_bins_broken.pdf'), dpi=300)
    plt.close(fig)

plot_token_usage_histogram_all_tools_broken(compare_result, bin_size=10000, clip_to_cap=False)

def plot_token_usage_histogram_all_tools_symlog(compare_result: dict, bin_size: int = 10000,
                                                clip_to_cap: bool = False,
                                                linthresh: float = 2.0):
    """Draw grouped histogram with a symmetric log y-scale to compress very tall bars
    (e.g., 0-10k) while relatively expanding mid/high bins for readability.

    linthresh controls the range around zero that remains linear.
    """
    tool_to_tokens = get_tool_tokens_all(compare_result, clip_to_cap=clip_to_cap)
    all_tokens = [t for tokens in tool_to_tokens.values() for t in tokens]
    if len(all_tokens) == 0:
        return
    max_token = max(all_tokens)
    # bin edges and labels consistent with other plots
    bin_edges = list(range(0, 60000 + 1, bin_size))
    if bin_edges[-1] != 60000:
        bin_edges = [0, 10000, 20000, 30000, 40000, 50000, 60000]
    bin_edges.append(65536)
    has_overflow = max_token > MAX_TOKENS_DS_V3
    if has_overflow:
        bin_edges.append(int(max_token) + 1)

    labels = []
    for i in range(len(bin_edges) - 1):
        left, right = bin_edges[i], bin_edges[i + 1]
        if has_overflow and i == len(bin_edges) - 2:
            labels.append("65k+")
        else:
            left_label = '0' if left == 0 else f"{left // 1000}k"
            right_label = f"{right // 1000}k"
            labels.append(f"{left_label}-{right_label}")

    tool_names = list(tool_to_tokens.keys())
    if len(tool_names) == 0:
        return

    counts_per_tool = []
    for tool in tool_names:
        tokens = np.array(tool_to_tokens[tool], dtype=int)
        if tokens.size == 0:
            counts = np.zeros(len(labels), dtype=int)
        else:
            counts, _ = np.histogram(tokens, bins=bin_edges)
        counts_per_tool.append(counts)

    num_bins = len(labels)
    x = np.arange(num_bins)
    num_tools = len(tool_names)
    total_width = min(0.8, 0.2 * num_tools)
    bar_width = total_width / max(1, num_tools)
    start = -total_width / 2

    plt.figure(figsize=(max(8, 1.2 * num_bins), 5))
    ax = plt.gca()
    for idx, tool in enumerate(tool_names):
        offsets = x + start + idx * bar_width
        bars = ax.bar(offsets, counts_per_tool[idx], width=bar_width, label=tool)
        for rect, value in zip(bars, counts_per_tool[idx], strict=False):
            height = rect.get_height()
            if value == 0:
                continue
            ax.text(
                rect.get_x() + rect.get_width() / 2.0,
                height,
                str(int(value)),
                ha='center',
                va='bottom',
                fontsize=8
            )
    ax.set_yscale('symlog', linthresh=linthresh)
    ax.yaxis.set_minor_formatter(plt.NullFormatter())
    plt.xticks(x, labels, rotation=30, ha='right')
    plt.xlabel('Prompt tokens (bins)')
    plt.ylabel('Count of results (symlog scale)')
    plt.title('Token usage distribution across results (symlog y-scale)')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join('plots', 'token_usage_hist_all_tools_10k_bins_symlog.pdf'), dpi=300)
    plt.close()

plot_token_usage_histogram_all_tools_symlog(compare_result, bin_size=10000, clip_to_cap=False, linthresh=2.0)
