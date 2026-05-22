import os
import yaml
from typing import Any
from datetime import date
from loguru import logger
from config import PROJECT_ROOT


class CrawlerConfig:
    def __init__(self, config_path: str | dict[str, Any]):
        """
        Initialize crawler configuration from a YAML file.

        Args:
            config_path: Path to the YAML configuration file or a config dict
        """

        self.project_root = str(PROJECT_ROOT)
        logger.debug(f"Project root: {self.project_root}")

        if isinstance(config_path, dict):
            self.config = config_path
        else:
            if os.path.isabs(config_path):
                full_config_path = config_path
            else:
                full_config_path = os.path.join(self.project_root, config_path)

            with open(full_config_path, encoding='utf-8') as file:
                self.config = yaml.safe_load(file)

        self._init_config()
        logger.add(self.log_file, rotation='1 GB', retention='7 days', level=self.log_level)

        logger.debug(
            f"\nRunning with config:\nLog file: {self.log_file}\n"
            f"Results file: {self.results_file}\n"
            f"Max retries: {self.max_retries}\n"
            f"Log level: {self.log_level}\n"
            f"Query nodes: {self.query_nodes}\n"
            f"Star ranges: {self.star_ranges}\n"
            f"Created after: {self.created_after}\n"
            f"Timeout: {self.timeout}\n"
            f"Languages: {self.languages}\n"
        )

    def _init_config(self):
        self.max_retries = self.config.get('max_retries', 3)
        self.log_level = self.config.get('log_level', 'INFO')
        min_stars = self.config.get('min_stars', 100)
        # self.star_ranges = self.auto_star_ranges(min_stars, 100000)
        self.star_ranges = [{'min': min_stars, 'max': None}]
        self.languages = self.config.get('languages', ['python'])
        self.created_after = self.config.get('created_after', '2022-01-01')
        self.created_until: str | None = self.config.get('created_until', None)
        self.timeout = self.config.get('timeout', 20)
        self.query_nodes = self.config.get('query_nodes', 30)
        self.log_file = self._resolve_path(self.config.get('log_file', 'logs/crawler.log'))
        self.results_file = self._resolve_path(self.config.get('results_file', 'results/repos.json'))
        self.results_template: str | None = self.config.get('results_template', None)

        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        os.makedirs(os.path.dirname(self.results_file), exist_ok=True)


    def _resolve_path(self, path):
        """Resolve relative paths to absolute paths"""
        if os.path.isabs(path):
            return path
        return os.path.join(self.project_root, path)

    def clone_with_overrides(self, overrides: dict[str, Any]) -> "CrawlerConfig":
        """Create a new CrawlerConfig by cloning current config and applying overrides."""
        new_config = dict(self.config)
        new_config.update(overrides)
        return CrawlerConfig(new_config)

    def build_results_file(self, language: str, start_date: date, end_date: date) -> str:
        """Build a results file path for a given language and date window.

        If results_template is provided, it supports placeholders: {lang}, {from}, {to}.
        Otherwise, it will generate a default path under the base results directory.
        """
        from_str = start_date.strftime('%Y%m%d')
        to_str = end_date.strftime('%Y%m%d')

        if self.results_template:
            relative_path = self.results_template.format(lang=language, from_=from_str, fromDate=from_str, to=to_str, toDate=to_str)
        else:
            base_dir = os.path.dirname(self.results_file)
            relative_path = os.path.join(base_dir, "crawled_repo_lists", language, f"repos_{language}_{from_str}_{to_str}.json")

        full_path = self._resolve_path(relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        return full_path

    def auto_star_ranges(min_stars=500, max_stars=50000):
        ranges = []
        cur = min_stars
        # 100-200: 2
        while cur < 200 and cur < max_stars:
            next_cur = min(cur + 2, 200, max_stars)
            ranges.append({"min": cur, "max": next_cur})
            cur = next_cur
        # 200-400: 3
        while cur < 400 and cur < max_stars:
            next_cur = min(cur + 3, 400, max_stars)
            ranges.append({"min": cur, "max": next_cur})
            cur = next_cur
        # 400-500: 10
        while cur < 600 and cur < max_stars:
            next_cur = min(cur + 5, 600, max_stars)
            ranges.append({"min": cur, "max": next_cur})
            cur = next_cur
        # 500-1000: 25
        while cur < 1000 and cur < max_stars:
            next_cur = min(cur + 15, 1000, max_stars)
            ranges.append({"min": cur, "max": next_cur})
            cur = next_cur
        # 1000-1500: 100
        while cur < 1500 and cur < max_stars:
            next_cur = min(cur + 50, 1500, max_stars)
            ranges.append({"min": cur, "max": next_cur})
            cur = next_cur
        # 1500-2000: 200
        while cur < 2000 and cur < max_stars:
            next_cur = min(cur + 100, 2000, max_stars)
            ranges.append({"min": cur, "max": next_cur})
            cur = next_cur
        # 2000-5000: 1000
        while cur < 5000 and cur < max_stars:
            next_cur = min(cur + 500, 5000, max_stars)
            ranges.append({"min": cur, "max": next_cur})
            cur = next_cur
        # 5000-20000: 5000
        while cur < 20000 and cur < max_stars:
            next_cur = min(cur + 1000, 20000, max_stars)
            ranges.append({"min": cur, "max": next_cur})
            cur = next_cur
        # 20000-50000: 10000
        while cur < 50000 and cur < max_stars:
            next_cur = min(cur + 10000, 50000, max_stars)
            ranges.append({"min": cur, "max": next_cur})
            cur = next_cur
        # 50000+
        if cur < max_stars or max_stars is None:
            ranges.append({"min": cur, "max": None})
        logger.info(f"Auto generated star ranges: {ranges}")
        return ranges


if __name__ == "__main__":
    # To use the config in code:
    config = CrawlerConfig("config/crawler.yaml")
    logger.debug(f"Log file: {config.log_file}")
