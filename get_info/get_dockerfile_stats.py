import requests
import json
import time
import os
from datetime import datetime
from loguru import logger
# Run with: python -m get_info.get_dockerfile_stats
from config import GITHUB_TOKENS

class GitHubDockerfileStats:
    def __init__(self, github_token: str | None = None):
        """
        Initialize GitHub Dockerfile statistics helper
        
        :param github_token: GitHub PAT; falls back to GITHUB_TOKEN env var
        """
        self.token = github_token or os.getenv('GITHUB_TOKEN')
        if not self.token:
            raise ValueError("GitHub token is required. Set GITHUB_TOKEN environment variable or pass it to constructor.")
        
        self.headers = {
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        self.base_url = "https://api.github.com"
        
    def search_code(self, query: str, max_results: int = 1000) -> tuple[int, list[dict]]:
        """
        Search code and return total count plus items
        
        :param query: search query
        :param max_results: max items to return
        :return: (total_count, items)
        """
        url = f"{self.base_url}/search/code"
        params = {
            'q': query,
            'per_page': 100,  # GitHub API page size cap
            'page': 1
        }
        
        all_results = []
        total_count = 0
        
        while len(all_results) < max_results:
            try:
                response = requests.get(url, headers=self.headers, params=params)
                
                if response.status_code == 403:
                    print("Rate limit exceeded. Waiting for reset...")
                    reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
                    if reset_time > 0:
                        wait_time = reset_time - time.time() + 10
                        if wait_time > 0:
                            print(f"Waiting {wait_time:.0f} seconds for rate limit reset...")
                            time.sleep(wait_time)
                            continue
                
                if response.status_code != 200:
                    print(f"Error: {response.status_code} - {response.text}")
                    break
                
                data = response.json()
                total_count = data.get('total_count', 0)
                items = data.get('items', [])
                
                if not items:
                    break
                
                all_results.extend(items)
                print(f"Fetched {len(all_results)} results, total available: {total_count}")
                
                # More pages?
                if len(items) < 100:
                    break
                
                params['page'] += 1
                
                # Avoid rate limits
                time.sleep(1)
                
            except Exception as e:
                print(f"Error during search: {e}")
                break
        
        return total_count, all_results[:max_results]
    
    def get_dockerfile_statistics(self) -> dict:
        """
        Collect Dockerfile-related search statistics
        
        :return: stats dict
        """
        print("Collecting Dockerfile statistics...")
        
        stats = {
            'timestamp': datetime.now().isoformat(),
            'searches': {}
        }
        
        # Fine-grained size buckets (each under ~100k results)
        size_ranges = [
            {'name': 'micro', 'query': 'size:<100', 'description': 'Dockerfiles under 100B'},
            {'name': 'tiny_1', 'query': 'size:100..150', 'description': '100B-150B Dockerfiles'},
            {'name': 'tiny_2', 'query': 'size:150..200', 'description': '150B-200B Dockerfiles'},
            {'name': 'tiny_3', 'query': 'size:200..250', 'description': '200B-250B Dockerfiles'},
            {'name': 'tiny_4', 'query': 'size:250..300', 'description': '250B-300B Dockerfiles'},
            {'name': 'tiny_5', 'query': 'size:300..350', 'description': '300B-350B Dockerfiles'},
            {'name': 'tiny_6', 'query': 'size:350..400', 'description': '350B-400B Dockerfiles'},
            {'name': 'tiny_7', 'query': 'size:400..450', 'description': '400B-450B Dockerfiles'},
            {'name': 'tiny_8', 'query': 'size:450..500', 'description': '450B-500B Dockerfiles'},
            {'name': 'very_small_1', 'query': 'size:500..600', 'description': '500B-600B Dockerfiles'},
            {'name': 'very_small_2', 'query': 'size:600..700', 'description': '600B-700B Dockerfiles'},
            {'name': 'very_small_3', 'query': 'size:700..800', 'description': '700B-800B Dockerfiles'},
            {'name': 'very_small_4', 'query': 'size:800..900', 'description': '800B-900B Dockerfiles'},
            {'name': 'very_small_5', 'query': 'size:900..1000', 'description': '900B-1KB Dockerfiles'},
            {'name': 'small_1', 'query': 'size:1000..1200', 'description': '1KB-1.2KB Dockerfiles'},
            {'name': 'small_2', 'query': 'size:1200..1400', 'description': '1.2KB-1.4KB Dockerfiles'},
            {'name': 'small_3', 'query': 'size:1400..1600', 'description': '1.4KB-1.6KB Dockerfiles'},
            {'name': 'small_4', 'query': 'size:1600..1800', 'description': '1.6KB-1.8KB Dockerfiles'},
            {'name': 'small_5', 'query': 'size:1800..2000', 'description': '1.8KB-2KB Dockerfiles'},
            {'name': 'small_medium_1', 'query': 'size:2000..2500', 'description': '2KB-2.5KB Dockerfiles'},
            {'name': 'small_medium_2', 'query': 'size:2500..3000', 'description': '2.5KB-3KB Dockerfiles'},
            {'name': 'small_medium_3', 'query': 'size:3000..3500', 'description': '3KB-3.5KB Dockerfiles'},
            {'name': 'small_medium_4', 'query': 'size:3500..4000', 'description': '3.5KB-4KB Dockerfiles'},
            {'name': 'small_medium_5', 'query': 'size:4000..4500', 'description': '4KB-4.5KB Dockerfiles'},
            {'name': 'small_medium_6', 'query': 'size:4500..5000', 'description': '4.5KB-5KB Dockerfiles'},
            {'name': 'medium_1', 'query': 'size:5000..6000', 'description': '5KB-6KB Dockerfiles'},
            {'name': 'medium_2', 'query': 'size:6000..7000', 'description': '6KB-7KB Dockerfiles'},
            {'name': 'medium_3', 'query': 'size:7000..8000', 'description': '7KB-8KB Dockerfiles'},
            {'name': 'medium_4', 'query': 'size:8000..9000', 'description': '8KB-9KB Dockerfiles'},
            {'name': 'medium_5', 'query': 'size:9000..10000', 'description': '9KB-10KB Dockerfiles'},
            {'name': 'medium_large_1', 'query': 'size:10000..12000', 'description': '10KB-12KB Dockerfiles'},
            {'name': 'medium_large_2', 'query': 'size:12000..14000', 'description': '12KB-14KB Dockerfiles'},
            {'name': 'medium_large_3', 'query': 'size:14000..16000', 'description': '14KB-16KB Dockerfiles'},
            {'name': 'medium_large_4', 'query': 'size:16000..18000', 'description': '16KB-18KB Dockerfiles'},
            {'name': 'medium_large_5', 'query': 'size:18000..20000', 'description': '18KB-20KB Dockerfiles'},
            {'name': 'large_1', 'query': 'size:20000..25000', 'description': '20KB-25KB Dockerfiles'},
            {'name': 'large_2', 'query': 'size:25000..30000', 'description': '25KB-30KB Dockerfiles'},
            {'name': 'large_3', 'query': 'size:30000..35000', 'description': '30KB-35KB Dockerfiles'},
            {'name': 'large_4', 'query': 'size:35000..40000', 'description': '35KB-40KB Dockerfiles'},
            {'name': 'large_5', 'query': 'size:40000..45000', 'description': '40KB-45KB Dockerfiles'},
            {'name': 'large_6', 'query': 'size:45000..50000', 'description': '45KB-50KB Dockerfiles'},
            {'name': 'very_large_1', 'query': 'size:50000..60000', 'description': '50KB-60KB Dockerfiles'},
            {'name': 'very_large_2', 'query': 'size:60000..70000', 'description': '60KB-70KB Dockerfiles'},
            {'name': 'very_large_3', 'query': 'size:70000..80000', 'description': '70KB-80KB Dockerfiles'},
            {'name': 'very_large_4', 'query': 'size:80000..90000', 'description': '80KB-90KB Dockerfiles'},
            {'name': 'very_large_5', 'query': 'size:90000..100000', 'description': '90KB-100KB Dockerfiles'},
            {'name': 'huge_1', 'query': 'size:100000..120000', 'description': '100KB-120KB Dockerfiles'},
            {'name': 'huge_2', 'query': 'size:120000..140000', 'description': '120KB-140KB Dockerfiles'},
            {'name': 'huge_3', 'query': 'size:140000..160000', 'description': '140KB-160KB Dockerfiles'},
            {'name': 'huge_4', 'query': 'size:160000..180000', 'description': '160KB-180KB Dockerfiles'},
            {'name': 'huge_5', 'query': 'size:180000..200000', 'description': '180KB-200KB Dockerfiles'},
            {'name': 'massive_1', 'query': 'size:200000..250000', 'description': '200KB-250KB Dockerfiles'},
            {'name': 'massive_2', 'query': 'size:250000..300000', 'description': '250KB-300KB Dockerfiles'},
            {'name': 'massive_3', 'query': 'size:300000..350000', 'description': '300KB-350KB Dockerfiles'},
            {'name': 'massive_4', 'query': 'size:350000..400000', 'description': '350KB-400KB Dockerfiles'},
            {'name': 'massive_5', 'query': 'size:400000..450000', 'description': '400KB-450KB Dockerfiles'},
            {'name': 'massive_6', 'query': 'size:450000..500000', 'description': '450KB-500KB Dockerfiles'},
            {'name': 'enormous', 'query': 'size:>500000', 'description': 'Dockerfiles over 500KB'}
        ]
        
        total_files = 0
        # total_repos = 0
        
        # Search by size bucket
        print("\n1. Searching Dockerfiles by size...")
        for size_range in size_ranges:
            print(f"   Searching {size_range['description']}...")
            query = f"filename:Dockerfile {size_range['query']}"
            
            try:
                count, results = self.search_code(query, max_results=1)  # count only
                
                stats['searches'][f"files_{size_range['name']}"] = {
                    'query': query,
                    'total_count': count,
                    'description': size_range['description']
                }
                
                total_files += count
                print(f"   Result: {count:,} files")
                
            except Exception as e:
                print(f"   Error: {e}")
                stats['searches'][f"files_{size_range['name']}"] = {
                    'query': query,
                    'error': str(e),
                    'description': size_range['description']
                }
        
        # Summary
        stats['summary'] = {
            'total_dockerfile_files': total_files,
        }
        
        # Per-bucket totals in summary
        for key, data in stats['searches'].items():
            if 'total_count' in data:
                stats['summary'][key] = data['total_count']
        
        return stats
    
    def save_results(self, stats: dict, filename: str | None = None) -> str:
        """
        Save stats to a JSON file
        
        :param stats: statistics dict
        :param filename: output name (auto if None)
        :return: path written
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"dockerfile_stats_{timestamp}.json"
        
        filepath = os.path.join("results", filename)
        os.makedirs("results", exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        
        print(f"\nSaved to: {filepath}")
        return filepath
    
    def print_summary(self, stats: dict):
        """
        Print a human-readable summary
        
        :param stats: statistics dict
        """
        print("\n" + "="*60)
        print("DOCKERFILE STATISTICS SUMMARY")
        print("="*60)
        
        summary = stats['summary']
        
        # Totals
        print(f"📊 Total Dockerfiles: {summary.get('total_dockerfile_files', 0):,}")

        
        # Size breakdown
        print("\n📁 Dockerfiles by size:")
        
        # Grouped buckets
        size_groups = {
            'Micro (<100B)': ['micro'],
            'Tiny (100B-500B)': ['tiny_1', 'tiny_2', 'tiny_3', 'tiny_4', 'tiny_5', 'tiny_6', 'tiny_7', 'tiny_8'],
            'Very Small (500B-1KB)': ['very_small_1', 'very_small_2', 'very_small_3', 'very_small_4', 'very_small_5'],
            'Small (1KB-2KB)': ['small_1', 'small_2', 'small_3', 'small_4', 'small_5'],
            'Small-Medium (2KB-5KB)': ['small_medium_1', 'small_medium_2', 'small_medium_3', 'small_medium_4', 'small_medium_5', 'small_medium_6'],
            'Medium (5KB-10KB)': ['medium_1', 'medium_2', 'medium_3', 'medium_4', 'medium_5'],
            'Medium-Large (10KB-20KB)': ['medium_large_1', 'medium_large_2', 'medium_large_3', 'medium_large_4', 'medium_large_5'],
            'Large (20KB-50KB)': ['large_1', 'large_2', 'large_3', 'large_4', 'large_5', 'large_6'],
            'Very Large (50KB-100KB)': ['very_large_1', 'very_large_2', 'very_large_3', 'very_large_4', 'very_large_5'],
            'Huge (100KB-200KB)': ['huge_1', 'huge_2', 'huge_3', 'huge_4', 'huge_5'],
            'Massive (200KB-500KB)': ['massive_1', 'massive_2', 'massive_3', 'massive_4', 'massive_5', 'massive_6'],
            'Enormous (>500KB)': ['enormous']
        }
        
        for group_name, categories in size_groups.items():
            group_total = 0
            for category in categories:
                file_key = f"files_{category}"
                if file_key in summary:
                    group_total += summary[file_key]
            if group_total > 0:
                print(f"   {group_name}: {group_total:,} files")
        
        print(f"\n⏰ Collected at: {stats['timestamp']}")
        print("="*60)


def main():
    """
    Entry point
    """
    print("GitHub Dockerfile stats: search by size bucket and count matches")
    print("="*40)
    
    # GitHub token
    github_token = GITHUB_TOKENS[0]
    if not github_token:
        logger.error("❌ Error: set GITHUB_TOKENS in .env")
        return
    
    try:
        # Run collector
        stats_tool = GitHubDockerfileStats(github_token)
        
        # Fetch stats
        stats = stats_tool.get_dockerfile_statistics()
        
        # Print summary
        stats_tool.print_summary(stats)
        
        # Save JSON
        stats_tool.save_results(stats)
        
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main() 