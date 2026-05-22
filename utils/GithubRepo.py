import re
import os
import git
import traceback
from pathlib import Path
from loguru import logger

class GithubRepo:
    def __init__(self, s: str):
        # Supports three input formats: web URL, owner/reponame, owner#reponame
        if s.startswith("http://") or s.startswith("https://"):
            match = re.match(r"https?://github.com/([^/]+)/([^/]+)(?:/)?", s)
            if not match:
                raise ValueError(f"Invalid GitHub URL: {s}")
            self.owner = match.group(1)
            self.reponame = match.group(2)
        else:
            # owner/reponame or owner#reponame
            match = re.match(r"([^/#]+)[/#]([^/#]+)", s)
            if not match:
                raise ValueError(f"Invalid GitHub repo string: {s}")
            self.owner = match.group(1)
            self.reponame = match.group(2)

        self.url_name = self.get_url_name()
        self.file_name = self.get_file_name()
        self.url = self.get_full_url()

    def get_url_name(self) -> str:
        return f"{self.owner}/{self.reponame}"

    def get_file_name(self) -> str:
        return f"{self.owner}#{self.reponame}"

    def get_full_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.reponame}"

def get_clean_repo(repo_path: str | git.Repo | Path | None) -> git.Repo | None:
    if isinstance(repo_path, git.Repo):
        repo = repo_path
    else:
        if os.path.isdir(repo_path): # type: ignore
            repo = git.Repo(repo_path)
        else:
            logger.error(f"Repo not found: {repo_path}")
            return None
    if repo is not None:
        try:
            repo.git.clean('-xdf')
            repo.git.reset('--hard', 'HEAD')
            branches = [branch.name for branch in repo.branches]
            stable_branches = ['mainline', 'main', 'master', 'development', 'develop', 'dev']
            flag = False
            for branch in stable_branches:
                if branch in branches:
                    repo.git.checkout(branch)
                    flag = True
                    break
            if not flag:
                logger.error(f"No stable branch {stable_branches} found for {repo_path}")
            repo.git.clean('-xdf')
            repo.git.reset('--hard', 'HEAD')
        except Exception as e:
            logger.error(f"Error checking out {stable_branches} branch for {repo_path}: {e}. {traceback.format_exc()}")
        return repo
    else:
        logger.error(f"Repo not found: {repo_path}")
        return None