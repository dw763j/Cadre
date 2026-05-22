import os
import json
from datetime import datetime
from collections import defaultdict

def parse_datetime(dt_str):
    """Parse ISO datetime."""
    return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))

def calculate_duration(created_at, updated_at):
    """Duration in seconds."""
    try:
        start_time = parse_datetime(created_at)
        end_time = parse_datetime(updated_at)
        duration = (end_time - start_time).total_seconds()
        # drop invalid durations
        if duration < 0 or duration > 86400:  # 24 hours
            return None
        return duration
    except Exception:
        return None

def format_duration(seconds):
    """Human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f} min"
    else:
        return f"{seconds/3600:.1f} h"

def to_markdown_table(headers, rows, title=None):
    md = ''
    if title:
        md += f'\n### {title}\n'
    # header
    md += '| ' + ' | '.join(headers) + ' |\n'
    md += '| ' + ' | '.join(['---'] * len(headers)) + ' |\n'
    # rows
    for row in rows:
        md += '| ' + ' | '.join(str(cell) for cell in row) + ' |\n'
    return md

def analyze_runs(workflow_runs_dir: str):
    total_success = 0
    total_failed = 0
    total_runs = 0
    repo_stats = {}
    workflow_stats = {}
    
    # event stats
    event_stats = defaultdict(lambda: {'success': 0, 'failed': 0, 'total': 0})
    
    # duration stats
    duration_stats = {
        'success': {'durations': [], 'total_time': 0},
        'failed': {'durations': [], 'total_time': 0}
    }
    
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
                
                # event name
                event = run.get('event', 'unknown')
                
                # duration
                created_at = run.get('created_at')
                updated_at = run.get('updated_at')
                duration = None
                if created_at and updated_at:
                    duration = calculate_duration(created_at, updated_at)
                
                wf_total += 1
                repo_total += 1
                event_stats[event]['total'] += 1
                
                if conclusion == 'success':
                    wf_success += 1
                    repo_success += 1
                    event_stats[event]['success'] += 1
                    if duration is not None:
                        duration_stats['success']['durations'].append(duration)
                        duration_stats['success']['total_time'] += duration
                else:
                    wf_failed += 1
                    repo_failed += 1
                    event_stats[event]['failed'] += 1
                    if duration is not None:
                        duration_stats['failed']['durations'].append(duration)
                        duration_stats['failed']['total_time'] += duration
            
            # per-workflow rollup
            if wf_total > 0:
                workflow_key = f"{repo_name}#{wf_id}"
                workflow_stats[workflow_key] = {
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

    # workflow table
    workflow_headers = ["Workflow", "Total", "Success", "Success rate", "Failed", "Fail rate"]
    workflow_rows = []
    for workflow, stats in sorted(workflow_stats.items(), key=lambda x: x[1]['success_rate'], reverse=True):
        workflow_rows.append([
            workflow,
            stats['total'],
            stats['success'],
            f"{stats['success_rate']:.2%}",
            stats['failed'],
            f"{stats['fail_rate']:.2%}"
        ])
    md = to_markdown_table(workflow_headers, workflow_rows, "Workflow statistics")
    
    # event table
    event_headers = ["Event", "Total", "Success", "Success rate", "Failed", "Fail rate"]
    event_rows = []
    for event, stats in sorted(event_stats.items(), key=lambda x: x[1]['total'], reverse=True):
        success_rate = stats['success'] / stats['total'] if stats['total'] > 0 else 0
        fail_rate = stats['failed'] / stats['total'] if stats['total'] > 0 else 0
        event_rows.append([
            event,
            stats['total'],
            stats['success'],
            f"{success_rate:.2%}",
            stats['failed'],
            f"{fail_rate:.2%}"
        ])
    md += to_markdown_table(event_headers, event_rows, "Event statistics")
    
    # duration table
    duration_headers = ["Status", "Count", "Total time", "Average", "Min", "Max"]
    duration_rows = []
    for status in ['success', 'failed']:
        durations = duration_stats[status]['durations']
        total_time = duration_stats[status]['total_time']
        count = len(durations)
        
        if count > 0:
            avg_duration = total_time / count
            min_duration = min(durations)
            max_duration = max(durations)
            
            duration_rows.append([
                f"{status.upper()} runs",
                count,
                format_duration(total_time),
                format_duration(avg_duration),
                format_duration(min_duration),
                format_duration(max_duration)
            ])
        else:
            duration_rows.append([
                f"{status.upper()} runs",
                count,
                "N/A",
                "N/A",
                "N/A",
                "N/A"
            ])
    md += to_markdown_table(duration_headers, duration_rows, "Run duration statistics")
    
    # overall
    md += f"\n### overall\n"
    md += f"- Total runs: {total_runs:,}\n"
    md += f"- Successful runs: {total_success:,} ({total_success/total_runs:.2%})\n"
    md += f"- Failed runs: {total_failed:,} ({total_failed/total_runs:.2%})\n"
    if workflow_stats:
        avg_workflow_success_rate = sum(stats['success_rate'] for stats in workflow_stats.values()) / len(workflow_stats)
        md += f"- Workflows: {len(workflow_stats):,}\n"
        md += f"- Mean workflow success rate: {avg_workflow_success_rate:.2%}\n"
    with open('results/view_analyze_results/workflow_runs_analysis.md', 'w', encoding='utf-8') as f:
        f.write(md)

if __name__ == "__main__":
    analyze_runs('results/workflow_runs')
