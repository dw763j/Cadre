import json
import os

def get_unified_comparable_results(result_paths: dict[str, str], compare_tool: str = 'dofix_v3'):
    results = {}
    status = set()
    skip_reasons = set()
    for name, path in result_paths.items():
        results[name] = {}
        for file in os.listdir(path):
            with open(os.path.join(path, file), encoding='utf-8') as f:
                data = json.load(f)
                for repair_info in data.get('builds', []):
                    results[name][repair_info['full_build_tag'].replace(repair_info['fix_method'], '')] = repair_info['status']
                    status.add(repair_info['status'])
                    if repair_info['status'] == 'skipped':
                        skip_reasons.add(repair_info.get('error', ''))
    # print(status)
    # print(list(skip_reasons), '\n\n')
    for name, result in results.items():
        results[name]['summary'] = {
            "success": sum(1 for status in result.values() if status == 'success'),
            "failed": sum(1 for status in result.values() if status == 'failed'),
            "skipped": sum(1 for status in result.values() if status == 'skipped'),
            "error": sum(1 for status in result.values() if status == 'error'),
            "timeout": sum(1 for status in result.values() if status == 'timeout'),
            "total": sum(1 for id in result.keys() if id != 'summary')
        }
        success_rate = results[name]['summary']['success'] / (results[name]['summary']['total'] - results[name]['summary']['skipped'])
        results[name]['summary']['success_rate'] = success_rate
        # print(name, results[name]['summary'])

    # print('\n\n')

    compare_list = {}
    for full_build_tag, status in results[compare_tool].items():
        if status != 'skipped':
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
                compare_result[name][full_build_tag] = 'missed'

    for name, _ in compare_result.items():
        compare_result[name]['common_success'] = []

    for name, result in compare_result.items():
        for full_build_tag, status in result.items():
            if full_build_tag != 'common_success' and full_build_tag in compare_list and compare_list[full_build_tag] == 'success' and status == 'success':
                compare_result[name]['common_success'].append(full_build_tag)

    for _name, result in compare_result.items():
        result['summary'] = {
            "success": sum(1 for status in result.values() if status == 'success'),
            "failed": sum(1 for status in result.values() if status == 'failed'),
            "skipped": sum(1 for status in result.values() if status == 'skipped'),
            "error": sum(1 for status in result.values() if status == 'error'),
            "timeout": sum(1 for status in result.values() if status == 'timeout'),
            "missed": sum(1 for status in result.values() if status == 'missed'),
            'rebuilding': sum(1 for status in result.values() if status == 'rebuilding'),
            "total": sum(1 for id in result.keys() if id != 'summary' or id != 'common_success')
        }
        result['summary']['success_rate'] = result['summary']['success'] / (result['summary']['total']) #  - result['summary']['skipped'])
        # print(name, result['summary'])
        # print(name, len(result['common_success']))
    
    return results, compare_result
