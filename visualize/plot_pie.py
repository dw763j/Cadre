import matplotlib.pyplot as plt
import os

# Light Morandi palette example
MORANDI_COLORS = [
    '#A3A9AA', '#B7AFA3', '#C1B7A3', '#A3B1B7', '#B7A3A9',
    '#A3B7AF', '#B7C1A3', '#C1A3B7', '#A3C1B7', '#B7A3C1'
]

def plot_pie(data_dict, output_path):
    labels = list(data_dict.keys())
    sizes = list(data_dict.values())
    plt.figure(figsize=(6, 6))
    patches, texts, autotexts = plt.pie(  # type: ignore
        sizes, autopct='%1.1f%%', startangle=90, textprops={'fontsize': 12}
    )
    plt.legend(patches, labels, loc='best', bbox_to_anchor=(1, 0.5))
    plt.axis('equal')
    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, format='pdf', bbox_inches='tight')
    plt.close()

if __name__ == '__main__':
    # Example usage
    sample_dict = {
        "pull_request": 3471,
        "workflow_dispatch": 71,
        "push": 1433,
        "release": 39,
        "schedule": 684,
        "pull_request_target": 65,
        "workflow_run": 5,
        "issue_comment": 12,
        "merge_group": 16,
        "watch": 89
    }
    plot_pie(sample_dict, 'results/visualization/pie_chart.pdf') 