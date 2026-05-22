# usage: python -m utils.check_repos [--cloned-repos PATH]
from loguru import logger
import os
from utils.GithubRepo import get_clean_repo
import argparse
from config import CLONED_REPOS_DIR


def check_repos_status(cloned_repos: str):
    failed_repos = []
    success_repos = []
    for repo in os.listdir(cloned_repos):
        repo_path = os.path.join(cloned_repos, repo)
        if os.path.isdir(repo_path):
            repo = get_clean_repo(repo_path)
            if repo is None:
                failed_repos.append(repo)
            else:
                success_repos.append(repo)
                logger.info(f"Checked {repo_path} successfully")
    logger.info(f"Failed repos: {len(failed_repos)}. Success repos: {len(success_repos)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check repos status")
    parser.add_argument("--cloned-repos", type=str, default=str(CLONED_REPOS_DIR), help="Cloned repos path")
    args = parser.parse_args()
    check_repos_status(args.cloned_repos)