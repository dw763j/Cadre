import requests
import json
import os
from datetime import datetime
from loguru import logger
import random
from time import sleep

from config import GITHUB_TOKENS
from utils.CrawlerConfig import CrawlerConfig
from utils.utils import TokenManager


class GitHubCrawler:
    def __init__(self, config_path: str | CrawlerConfig):
        self.config = config_path if isinstance(config_path, CrawlerConfig) else CrawlerConfig(config_path)
        self.token_manager = TokenManager(GITHUB_TOKENS)
        self.api_endpoint = 'https://api.github.com/graphql'

    def get_headers(self):
        return {
            'Authorization': f'Bearer {self.token_manager.get_token()}',
            'Content-Type': 'application/json',
        }

    def execute_query(self, query, variables=None):
        """Excute GraphQL query with retry"""
        retry_count = 0

        while retry_count < self.config.max_retries:
            try:
                json_data = {
                    'query': query,
                    'variables': variables or {}
                }
                sleep(random.uniform(0, 1))
                response = requests.post(
                    self.api_endpoint,
                    headers=self.get_headers(),
                    json=json_data,
                    timeout=self.config.timeout
                )

                if response.status_code == 200:
                    # extract rate limit info
                    remaining = response.headers.get('X-RateLimit-Remaining', 'N/A')
                    reset_time = response.headers.get('X-RateLimit-Reset', 'N/A')
                    if reset_time != 'N/A':
                        reset_time = datetime.fromtimestamp(int(reset_time)).strftime('%Y-%m-%d %H:%M:%S')
                    logger.debug(f"Rate limit remaining: {remaining}, Reset time: {reset_time}")
                    return response.json(), remaining, reset_time
                else:
                    raise Exception(f"Query failed with status code: {response.status_code}")

            except (requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    Exception) as e:
                retry_count += 1
                if retry_count == self.config.max_retries:
                    logger.error(f"Failed after {self.config.max_retries} retries: {str(e)}")
                logger.error(f"Request failed, retrying ({retry_count}/{self.config.max_retries})...")
                import time
                time.sleep(2 ** retry_count)
        return None, None, None

    def search_repos(self, cursor=None, min_stars=None, max_stars=None):
        query = """
        query SearchRepos($cursor: String) {
          search(
            query: "####"
            type: REPOSITORY
            first: ****
            after: $cursor
          ) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              ... on Repository {
                url
                stargazerCount
                object(expression: "HEAD:Dockerfile") {
                  ... on Blob {
                    id
                  }
                }
              }
            }
          }
        }
        """
        min_stars_value = min_stars if min_stars is not None else self.config.min_stars # type: ignore

        # star range query
        if max_stars:
            stars_query = f"stars:{min_stars_value}..{max_stars}"
        else:
            stars_query = f"stars:>{min_stars_value}"

        # build created range
        if getattr(self.config, 'created_until', None):
            created_filter = f"created:{self.config.created_after}..{self.config.created_until}"
        else:
            created_filter = f"created:>{self.config.created_after}"

        # language filter - expect one language per run but support list
        languages = self.config.languages or []
        language_filter = "" if not languages else f" language:{' language:'.join(languages)}"

        search_str = f"{stars_query}{language_filter} {created_filter} fork:false sort:stars-desc"
        query = query.replace('####', search_str)

        query = query.replace('****', str(self.config.query_nodes))
        variables = {'cursor': cursor}
        result, remaining, reset_time = self.execute_query(query, variables)
        if not result:
            return None, None, None
        else:
            return result['data']['search'], remaining, reset_time

    def save_results(self, data_dict, filename):
        if not os.path.exists(os.path.dirname(filename)):
            os.makedirs(os.path.dirname(filename))

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data_dict, f, ensure_ascii=False, indent=2)

    def load_previous_results(self, filepath):
        """Load previous results if file exists"""
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            try:
                with open(filepath, encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"Loaded previous results from {filepath}")
                return data
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse previous results file: {filepath}")
        return None

    def crawl(self):
        """Main entry point with star range segmentation; supports automatic range splitting."""
        config_dict = {k: v for k, v in self.config.config.items() if k != 'token' and k != 'created_after'}
        config_dict['created_after'] = str(self.config.created_after)
        config_dict['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        result_path = self.config.results_file
        all_repos_path = os.path.splitext(result_path)[0] + '_all.json'
        star_ranges = self.config.star_ranges
        # Try to load previous results
        previous_results = self.load_previous_results(result_path)
        previous_all_results = self.load_previous_results(all_repos_path)

        # Initialize or restore results
        if previous_results:
            results = previous_results
            # Create a set of repository URLs for quick duplicate checking
            existing_repos_urls = {repo["url"] for repo in previous_results["repos"]}
            logger.info(f"Loaded {len(previous_results['repos'])} existing repos")
        else:
            results = {"config": config_dict, "repos": []}
            existing_repos_urls = set()

        # Initialize or restore all_results
        if previous_all_results:
            all_results = previous_all_results
            all_existing_repos_urls = {repo["url"] for repo in previous_all_results["all_repos"]}
            logger.info(f"Loaded {len(previous_all_results['all_repos'])} existing all repos")
        else:
            all_results = {"config": config_dict, "all_repos": []}
            all_existing_repos_urls = set()

        current_range_index = 0
        if previous_results and "current_range_index" in previous_results:
            current_range_index = previous_results["current_range_index"]

        for range_index in range(current_range_index, len(star_ranges)):
            min_stars = star_ranges[range_index].get('min', 0)
            max_stars = star_ranges[range_index].get('max', None)
            logger.info(f"Processing star range: {min_stars} to {max_stars if max_stars else 'unlimited'}")
            results["current_range_index"] = range_index  # save current range index

            cursor_key = f"cursor_range_{range_index}"
            cursor = None
            if previous_results and cursor_key in previous_results:
                cursor = previous_results[cursor_key]
                logger.info(f"Resuming with cursor: {cursor}")

            while True:
                repo_count, all_repo_count = len(results['repos']), len(all_results['all_repos'])
                logger.info(f"Total repos found: {repo_count}, All repos found: {all_repo_count}, Range: {min_stars}-{max_stars}")

                result, remaining, reset_time = self.search_repos(cursor, min_stars, max_stars)
                if not result:
                    logger.error(f"Failed to fetch repos for range {min_stars}-{max_stars}")
                    break

                if remaining and int(remaining) < 10:
                    logger.warning(f"Rate limit reached, reset time: {reset_time}")
                    logger.warning("Saving current progress and exiting")

                    # Save progress before exiting due to rate limit
                    results[cursor_key] = cursor

                    self.save_results(results, result_path)
                    self.save_results(all_results, all_repos_path)
                    raise requests.exceptions.RequestException("GitHub API rate limit reached. Resume later.")

                # Process new repositories
                for repo in result['nodes']:
                    repo_url = repo['url']
                    info = {
                        "url": repo_url,
                        "stars": repo['stargazerCount'],
                        "Dockerfile": repo['object'] is not None,
                    }
                    # Add to all_repos if not a duplicate
                    if repo_url not in all_existing_repos_urls:
                        all_results['all_repos'].append(info) # type: ignore
                        all_existing_repos_urls.add(repo_url)

                    # Check if repo meets criteria and is not a duplicate
                    if (repo['object'] is not None and repo_url not in existing_repos_urls):

                        results['repos'].append(info) # type: ignore
                        existing_repos_urls.add(repo_url)

                if not result['pageInfo']['hasNextPage']:
                    logger.info(f"No more pages for range {min_stars}-{max_stars}")
                    if cursor_key in results:
                        del results[cursor_key]  # del cursor for finished range
                    break

                cursor = result['pageInfo']['endCursor']
                results[cursor_key] = cursor

                # save intermediate results every page
                self.save_results(results, result_path)
                self.save_results(all_results, all_repos_path)

        # Final save without cursor as we're done
        if "current_range_index" in results:
            del results["current_range_index"]

        for i in range(len(star_ranges)):
            cursor_key = f"cursor_range_{i}"
            if cursor_key in results:
                del results[cursor_key]

        self.save_results(results, result_path)
        self.save_results(all_results, all_repos_path)
        return results


if __name__ == '__main__':
    try:
        crawler = GitHubCrawler('config/my_crawler.yml')
        repos = crawler.crawl()
        logger.info(f"Total repos found: {len(repos['repos'])}")
    except Exception as e:
        logger.error(f"Crawler failed: {str(e)}")
        import sys
        sys.exit(1)
