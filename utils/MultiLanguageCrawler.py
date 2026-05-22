# python -m utils.MultiLanguageCrawler

from __future__ import annotations

from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from loguru import logger

from utils.CrawlerConfig import CrawlerConfig
from utils.GitHubCrawler import GitHubCrawler


def _month_end(d: date) -> date:
    # Move to first of next month, then step back one day
    if d.month == 12:
        nxt = date(d.year + 1, 1, 1)
    else:
        nxt = date(d.year, d.month + 1, 1)
    return nxt - timedelta(days=1)


def generate_month_windows(end_inclusive: datetime, start_exclusive: datetime) -> list[tuple[datetime, datetime]]:
    """Generate [start, end] monthly windows going backward from end_inclusive to start_exclusive.

    Each window is inclusive on both ends at day granularity. The last window's start
    will be strictly greater than start_exclusive.
    """
    windows: list[tuple[datetime, datetime]] = []
    current_end_date = end_inclusive.date()
    lower_bound_date = start_exclusive.date()

    while current_end_date > lower_bound_date:
        month_start = date(current_end_date.year, current_end_date.month, 1)
        window_start_date = max(lower_bound_date + timedelta(days=1), month_start)
        window_end_date = min(current_end_date, _month_end(month_start))

        if window_start_date > window_end_date:
            # No valid range for this month; move to previous day
            current_end_date = month_start - timedelta(days=1)
            continue

        windows.append(
            (
                datetime.combine(window_start_date, datetime.min.time()),
                datetime.combine(window_end_date, datetime.max.time()),
            )
        )

        # Move to the day before the start of this month
        current_end_date = month_start - timedelta(days=1)

    # Ensure newest-first order
    windows.sort(key=lambda x: x[1], reverse=True)
    return windows


def run_language_in_windows(
    base_config: CrawlerConfig,
    language: str,
    start_exclusive: datetime,
    end_inclusive: datetime,
) -> list[str]:
    """Run crawler for a single language across monthly windows from end to start.

    Returns a list of result file paths for this language.
    """
    result_files: list[str] = []
    windows = generate_month_windows(end_inclusive, start_exclusive)
    logger.info(f"Language {language}: {len(windows)} monthly windows to crawl")

    for window_start, window_end in windows:
        overrides = {
            "languages": [language],
            "created_after": window_start.strftime("%Y-%m-%d"),
            "created_until": window_end.strftime("%Y-%m-%d"),
        }
        # Use results template if available to produce per-language, per-window files
        results_path = base_config.build_results_file(
            language,
            start_date=window_start.date(),
            end_date=window_end.date(),
        )
        overrides["results_file"] = results_path

        lang_cfg = base_config.clone_with_overrides(overrides)
        crawler = GitHubCrawler(lang_cfg)
        logger.info(
            f"Start window for {language}: {overrides['created_after']}..{overrides['created_until']} -> {results_path}"
        )
        try:
            crawler.crawl()
        except Exception as e:
            logger.error(f"Window failed for {language} {overrides['created_after']}..{overrides['created_until']}: {str(e)}")
            # Persist what we can and continue to next window
        result_files.append(results_path)

    return result_files


def run_crawlers_by_language(base_config_path: str, max_workers: int | None = None) -> dict[str, list[str]]:
    """Entry point: run one crawler per language over monthly windows with concurrency.

    Args:
        base_config_path: Path to the base configuration file
        max_workers: Maximum number of concurrent language crawlers. 
                    If None, uses min(len(languages), 4) to avoid overwhelming GitHub API

    Returns:
        A mapping from language to its list of results file paths.
    """
    base_cfg = CrawlerConfig(base_config_path)

    end_inclusive = datetime.now()
    start_exclusive = datetime.fromisoformat(str(base_cfg.created_after))

    # Determine optimal concurrency level
    if max_workers is None:
        max_workers = min(len(base_cfg.languages), 4)  # Cap at 4 to respect rate limits
    
    logger.info(f"Starting multi-language crawl with {max_workers} concurrent workers for {len(base_cfg.languages)} languages")

    outputs: dict[str, list[str]] = {}
    
    # Use ThreadPoolExecutor for language-level concurrency
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all language crawl jobs
        future_to_lang = {
            executor.submit(run_language_in_windows, base_cfg, lang, start_exclusive, end_inclusive): lang
            for lang in base_cfg.languages
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_lang):
            lang = future_to_lang[future]
            try:
                result_files = future.result()
                outputs[lang] = result_files
                logger.info(f"Completed crawl for language '{lang}': {len(result_files)} result files")
            except Exception as exc:
                logger.error(f"Language '{lang}' crawl failed: {exc}")
                outputs[lang] = []  # Empty list for failed languages

    logger.info(f"Multi-language crawl completed. Results: {list(outputs.keys())}")
    return outputs


def run_crawlers_by_language_sequential(base_config_path: str) -> dict[str, list[str]]:
    """Sequential version of run_crawlers_by_language for comparison or when concurrency is not desired."""
    base_cfg = CrawlerConfig(base_config_path)

    end_inclusive = datetime.now()
    start_exclusive = datetime.fromisoformat(str(base_cfg.created_after))

    outputs: dict[str, list[str]] = {}
    for lang in base_cfg.languages:
        outputs[lang] = run_language_in_windows(base_cfg, lang, start_exclusive, end_inclusive)

    return outputs


if __name__ == "__main__":
    final_results = run_crawlers_by_language("config/crawler.yaml", max_workers=8)

    with open("results/crawled_repo_lists/final_results.json", "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=2)