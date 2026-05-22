import os
import json
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np

# Set style for beautiful plots
plt.style.use('seaborn-v0_8')
sns.set_palette("husl")

def load_workflow_data(workflow_runs_dir: str):
    """Load and process workflow run data"""
    repo_stats = {}
    workflow_stats = {}
    total_success = 0
    total_failed = 0
    total_runs = 0
    
    for filename in os.listdir(workflow_runs_dir):
        if not filename.endswith('#runs.json'):
            continue
        repo_name = filename.replace('#runs.json', '').replace('#', '/')
        file_path = os.path.join(workflow_runs_dir, filename)
        try:
            with open(file_path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"[ERROR] Failed to load {file_path}: {e}")
            continue
            
        repo_success = 0
        repo_failed = 0
        repo_total = 0
        
        for wf_id, wf_data in data.items():
            runs = wf_data.get('runs', [])
            wf_success = 0
            wf_failed = 0
            wf_total = 0
            
            for run in runs:
                conclusion = run.get('conclusion')
                if conclusion == 'skipped':
                    continue
                wf_total += 1
                repo_total += 1
                if conclusion == 'success':
                    wf_success += 1
                    repo_success += 1
                else:
                    wf_failed += 1
                    repo_failed += 1
            
            if wf_total > 0:
                workflow_key = f"{repo_name}#{wf_id}"
                workflow_stats[workflow_key] = {
                    'repo': repo_name,
                    'workflow_id': wf_id,
                    'success': wf_success,
                    'failed': wf_failed,
                    'total': wf_total,
                    'success_rate': wf_success / wf_total,
                    'fail_rate': wf_failed / wf_total
                }
        
        total_success += repo_success
        total_failed += repo_failed
        total_runs += repo_total
        if repo_total > 0:
            repo_stats[repo_name] = {
                'success': repo_success,
                'failed': repo_failed,
                'total': repo_total,
                'success_rate': repo_success / repo_total,
                'fail_rate': repo_failed / repo_total
            }
    
    return repo_stats, workflow_stats, total_success, total_failed, total_runs

def create_visualizations(repo_stats, workflow_stats, total_success, total_failed, total_runs):
    """Create comprehensive visualizations"""
    
    # Create figure with subplots
    fig = plt.figure(figsize=(20, 20))
    
    # 1. Overall Success Rate Pie Chart
    ax1 = plt.subplot(3, 3, 1)
    labels = ['Successful', 'Failed']
    sizes = [total_success, total_failed]
    colors = ['#2E8B57', '#DC143C']
    explode = (0.05, 0)
    
    pie_result = ax1.pie(sizes, explode=explode, labels=labels, colors=colors,
                         autopct='%1.1f%%', startangle=90, shadow=True)
    ax1.set_title('Overall Workflow Run Success Rate', fontsize=14, fontweight='bold', pad=20)
    
    # 2. Repository Success Rates (Top 15)
    ax2 = plt.subplot(3, 3, 2)
    repo_df = pd.DataFrame.from_dict(repo_stats, orient='index')
    repo_df = repo_df.sort_values('total', ascending=False).head(15)
    
    bars = ax2.barh(range(len(repo_df)), repo_df['success_rate'], 
                    color=sns.color_palette("viridis", len(repo_df)))
    ax2.set_yticks(range(len(repo_df)))
    ax2.set_yticklabels([repo.split('/')[-1] for repo in repo_df.index], fontsize=8)
    ax2.set_xlabel('Success Rate')
    ax2.set_title('Top 15 Repositories by Total Runs\n(Success Rate)', fontsize=12, fontweight='bold')
    ax2.grid(axis='x', alpha=0.3)
    
    # 3. Workflow Success Rate Distribution (Histogram)
    ax3 = plt.subplot(3, 3, 3)
    success_rates = [stats['success_rate'] for stats in workflow_stats.values()]
    
    ax3.hist(success_rates, bins=20, alpha=0.7, color='skyblue', edgecolor='black', density=True)
    ax3.axvline(float(np.mean(success_rates)), color='red', linestyle='--', 
                label=f'Mean: {np.mean(success_rates):.2%}')
    ax3.axvline(float(np.median(success_rates)), color='orange', linestyle='--', 
                label=f'Median: {np.median(success_rates):.2%}')
    ax3.set_xlabel('Success Rate')
    ax3.set_ylabel('Density')
    ax3.set_title('Distribution of Workflow Success Rates (Histogram)', fontsize=12, fontweight='bold')
    ax3.legend()
    ax3.grid(alpha=0.3)
    
    # 4. Kernel Density Estimation Plot
    ax4 = plt.subplot(3, 3, 4)
    
    # Create KDE plot
    sns.kdeplot(data=success_rates, ax=ax4, color='purple', linewidth=2, fill=True, alpha=0.3)
    
    # Add vertical lines for mean and median
    ax4.axvline(float(np.mean(success_rates)), color='red', linestyle='--', linewidth=2,
                label=f'Mean: {np.mean(success_rates):.2%}')
    ax4.axvline(float(np.median(success_rates)), color='orange', linestyle='--', linewidth=2,
                label=f'Median: {np.median(success_rates):.2%}')
    
    # Add percentiles
    p25, p75 = np.percentile(success_rates, [25, 75])
    ax4.axvline(p25, color='green', linestyle=':', linewidth=1.5,
                label=f'25th percentile: {p25:.2%}')
    ax4.axvline(p75, color='green', linestyle=':', linewidth=1.5,
                label=f'75th percentile: {p75:.2%}')
    
    ax4.set_xlabel('Success Rate')
    ax4.set_ylabel('Density')
    ax4.set_title('Kernel Density Estimation of Success Rates', fontsize=12, fontweight='bold')
    ax4.legend(fontsize=9)
    ax4.grid(alpha=0.3)
    
    # 5. Success Rate vs Total Runs Scatter Plot
    ax5 = plt.subplot(3, 3, 5)
    workflow_df = pd.DataFrame.from_dict(workflow_stats, orient='index')
    
    scatter = ax5.scatter(workflow_df['total'], workflow_df['success_rate'], 
                         alpha=0.6, c=workflow_df['total'], cmap='viridis', s=50)
    ax5.set_xlabel('Total Runs')
    ax5.set_ylabel('Success Rate')
    ax5.set_title('Success Rate vs Total Runs', fontsize=12, fontweight='bold')
    ax5.grid(alpha=0.3)
    
    # Add colorbar
    cbar = plt.colorbar(scatter, ax=ax5)
    cbar.set_label('Total Runs')
    
    # 6. Top 20 Workflows by Success Rate
    ax6 = plt.subplot(3, 3, 6)
    top_workflows = workflow_df.sort_values('success_rate', ascending=False).head(20)
    
    bars = ax6.barh(range(len(top_workflows)), top_workflows['success_rate'],
                    color=sns.color_palette("plasma", len(top_workflows)))
    ax6.set_yticks(range(len(top_workflows)))
    ax6.set_yticklabels([f"{wf.split('#')[-1]}" for wf in top_workflows.index], fontsize=7)
    ax6.set_xlabel('Success Rate')
    ax6.set_title('Top 20 Workflows by Success Rate', fontsize=12, fontweight='bold')
    ax6.grid(axis='x', alpha=0.3)
    
    # 7. Box Plot of Success Rates
    ax7 = plt.subplot(3, 3, 7)
    
    # Create box plot
    box_plot = ax7.boxplot(success_rates, patch_artist=True, 
                          boxprops=dict(facecolor='lightblue', alpha=0.7),
                          medianprops=dict(color='red', linewidth=2),
                          flierprops=dict(marker='o', markerfacecolor='red', markersize=4))
    
    ax7.set_ylabel('Success Rate')
    ax7.set_title('Box Plot of Workflow Success Rates', fontsize=12, fontweight='bold')
    ax7.grid(axis='y', alpha=0.3)
    
    # Add statistics text
    stats_text = f'Mean: {np.mean(success_rates):.2%}\nMedian: {np.median(success_rates):.2%}\nStd: {np.std(success_rates):.2%}'
    ax7.text(0.02, 0.98, stats_text, transform=ax7.transAxes, fontsize=10,
             verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
    
    # 8. Success Rate Distribution by Run Count Category
    ax8 = plt.subplot(3, 3, 8)
    
    # Categorize workflows by run count
    workflow_df['run_category'] = pd.cut(workflow_df['total'], 
                                        bins=[0, 10, 50, 100, 500, float('inf')],
                                        labels=['1-10', '11-50', '51-100', '101-500', '500+'])
    
    # Create violin plot
    sns.violinplot(data=workflow_df, x='run_category', y='success_rate', ax=ax8)
    ax8.set_xlabel('Number of Runs')
    ax8.set_ylabel('Success Rate')
    ax8.set_title('Success Rate Distribution by Run Count', fontsize=12, fontweight='bold')
    ax8.grid(axis='y', alpha=0.3)
    
    # 9. Summary Statistics Table
    ax9 = plt.subplot(3, 3, 9)
    ax9.axis('tight')
    ax9.axis('off')
    
    # Calculate statistics
    avg_workflow_success_rate = np.mean(success_rates)
    median_workflow_success_rate = np.median(success_rates)
    std_workflow_success_rate = np.std(success_rates)
    p25, p75 = np.percentile(success_rates, [25, 75])
    
    summary_data = [
        ['Metric', 'Value'],
        ['Total Workflow Runs', f'{total_runs:,}'],
        ['Successful Runs', f'{total_success:,}'],
        ['Failed Runs', f'{total_failed:,}'],
        ['Overall Success Rate', f'{total_success/total_runs:.2%}'],
        ['Number of Workflows', f'{len(workflow_stats):,}'],
        ['Number of Repositories', f'{len(repo_stats):,}'],
        ['Avg Workflow Success Rate', f'{avg_workflow_success_rate:.2%}'],
        ['Median Workflow Success Rate', f'{median_workflow_success_rate:.2%}'],
        ['Std Dev Success Rate', f'{std_workflow_success_rate:.2%}'],
        ['25th Percentile', f'{p25:.2%}'],
        ['75th Percentile', f'{p75:.2%}']
    ]
    
    table = ax9.table(cellText=summary_data[1:], colLabels=summary_data[0],
                     cellLoc='left', loc='center', colWidths=[0.6, 0.4])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.2, 1.5)
    
    # Style the table
    for i in range(len(summary_data)):
        for j in range(2):
            if i == 0:  # Header row
                table[(i, j)].set_facecolor('#4CAF50')
                table[(i, j)].set_text_props(weight='bold', color='white')
            else:
                table[(i, j)].set_facecolor('#f0f0f0' if i % 2 == 0 else 'white')
    
    ax9.set_title('Summary Statistics', fontsize=14, fontweight='bold', pad=20)
    
    plt.tight_layout()
    plt.savefig('workflow_analysis.png', dpi=300, bbox_inches='tight', 
                facecolor='white', edgecolor='none')
    plt.show()
    
    # Create additional detailed charts
    create_detailed_charts(workflow_stats, repo_stats)

def create_detailed_charts(workflow_stats, repo_stats):
    """Create additional detailed visualizations"""
    
    # 1. Repository Performance Heatmap
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
    
    # Top repositories heatmap
    repo_df = pd.DataFrame.from_dict(repo_stats, orient='index')
    top_repos = repo_df.sort_values('total', ascending=False).head(20)
    
    # Create heatmap data
    heatmap_data = top_repos[['success_rate', 'fail_rate']].T
    sns.heatmap(heatmap_data, annot=True, fmt='.2%', cmap='RdYlGn', 
                cbar_kws={'label': 'Rate'}, ax=ax1)
    ax1.set_title('Top 20 Repositories: Success vs Failure Rates', fontweight='bold')
    ax1.set_xlabel('Repository')
    ax1.set_ylabel('Rate Type')
    
    # 2. Workflow Performance by Run Count
    workflow_df = pd.DataFrame.from_dict(workflow_stats, orient='index')
    
    # Categorize workflows by run count
    workflow_df['run_category'] = pd.cut(workflow_df['total'], 
                                        bins=[0, 10, 50, 100, 500, float('inf')],
                                        labels=['1-10', '11-50', '51-100', '101-500', '500+'])
    
    category_stats = workflow_df.groupby('run_category')['success_rate'].agg(['mean', 'count'])
    
    bars = ax2.bar(range(len(category_stats)), category_stats['mean'], 
                   color=sns.color_palette("Set3", len(category_stats)))
    ax2.set_xlabel('Number of Runs')
    ax2.set_ylabel('Average Success Rate')
    ax2.set_title('Average Success Rate by Run Count Category', fontweight='bold')
    ax2.set_xticks(range(len(category_stats)))
    ax2.set_xticklabels(category_stats.index)
    ax2.grid(axis='y', alpha=0.3)
    
    # Add count labels on bars
    for i, (bar, count) in enumerate(zip(bars, category_stats['count'], strict=False)):
        height = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                f'n={count}', ha='center', va='bottom', fontweight='bold')
    
    plt.tight_layout()
    plt.savefig('detailed_analysis.png', dpi=300, bbox_inches='tight', 
                facecolor='white', edgecolor='none')
    plt.show()

def main():
    """Main function to run the analysis and create visualizations"""
    print("Loading workflow data...")
    repo_stats, workflow_stats, total_success, total_failed, total_runs = load_workflow_data('workflow_runs')
    
    print(f"Loaded data for {len(repo_stats)} repositories and {len(workflow_stats)} workflows")
    print("Creating visualizations...")
    
    create_visualizations(repo_stats, workflow_stats, total_success, total_failed, total_runs)
    
    print("Visualizations saved as 'workflow_analysis.png' and 'detailed_analysis.png'")

if __name__ == "__main__":
    main() 