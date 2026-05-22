"""Project-level runtime configuration package."""

import os
from collections.abc import Iterable
from pathlib import Path

from loguru import logger

# Repository root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Docker build runtime parameters
MAX_BUILDERS = 4
REGISTRY_PORT = 5001

# Common result and workspace paths
RESULTS_DIR = PROJECT_ROOT / "results"
RESULTS_MULTIPLE_DIR = PROJECT_ROOT / "results_multiple"
CLONED_REPOS_DIR = RESULTS_DIR / "cloned_repos"
CONFIG_DIR = PROJECT_ROOT / "config"
BUILD_LOGS_DIRNAME = "build_logs"
RUN_LOGS_DIRNAME = "run_logs"
RESULTS_SUBDIRNAME = "results"
BUILD_DOCKERFILE_DIR = PROJECT_ROOT / "build_dockerfile"
BUILD_DOCKERFILE_LOG_DIRS = (BUILD_LOGS_DIRNAME, RUN_LOGS_DIRNAME)
BUILD_DOCKERFILE_BUILD_LOGS_DIR = BUILD_DOCKERFILE_DIR / BUILD_LOGS_DIRNAME
BUILD_DOCKERFILE_RUN_LOGS_DIR = BUILD_DOCKERFILE_DIR / RUN_LOGS_DIRNAME

# Default dataset and fixed Dockerfile paths used by build_fixed_docker.py
DEFAULT_DATASET_PATH = RESULTS_DIR / "dataset_valid_fixed_params_with_dockerfile.json"
# Default relative to RESULTS_DIR: fixed_dockerfiles/dofix/remove_build_channel/DeepSeek-V3, etc.
# DEFAULT_FIXED_DOCKERFILE_BASE_PATH = RESULTS_DIR / "fixed_dockerfiles" / "dofix" / "remove_build_channel" / "DeepSeek-V3"
# DEFAULT_FIXED_DOCKER_BUILD_OUTPUT_DIR = RESULTS_DIR / "fixed_docker_builds" / "dofix" / "remove_build_channel" / "DeepSeek-V3"

FIXED_DOCKERFILES_DIRNAME = "fixed_dockerfiles"
FIXED_DOCKER_BUILDS_DIRNAME = "fixed_docker_builds"
REPAIRING_DOCKERFILE_DIRNAME = "repairing_dockerfile"


def slug_model(model: str) -> str:
    """Replace / in model names with _ to match repair artifact directories and tags."""
    return model.replace("/", "_")


def results_fixed_dockerfiles_dir(method: str, mode: str, model: str, *, results_root: Path | None = None) -> Path:
    base = results_root if results_root is not None else RESULTS_DIR
    if method == "parfum":
        return base / FIXED_DOCKERFILES_DIRNAME / method
    return base / FIXED_DOCKERFILES_DIRNAME / method / mode / slug_model(model)


def results_fixed_docker_builds_dir(method: str, mode: str, model: str, *, results_root: Path | None = None) -> Path:
    base = results_root if results_root is not None else RESULTS_DIR
    if method == "parfum":
        return base / FIXED_DOCKER_BUILDS_DIRNAME / method
    return base / FIXED_DOCKER_BUILDS_DIRNAME / method / mode / slug_model(model)


def results_repairing_dockerfile_dir(method: str, mode: str, model: str, *, results_root: Path | None = None) -> Path:
    base = results_root if results_root is not None else RESULTS_DIR
    if method == "parfum":
        return base / REPAIRING_DOCKERFILE_DIRNAME / method
    return base / REPAIRING_DOCKERFILE_DIRNAME / method / mode / slug_model(model)


def _load_env() -> None:
    env_file = PROJECT_ROOT / ".env.local"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()


def env_get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_get_list(key: str, default: list[str] | None = None, sep: str = ",") -> list[str]:
    raw_value = os.environ.get(key, "")
    if not raw_value:
        return list(default or [])
    return [item.strip() for item in raw_value.split(sep) if item.strip()]


# LLM API (OpenAI-compatible)
LLM_API_KEY = env_get("LLM_API_KEY")
LLM_API_BASE = env_get("LLM_API_BASE", "https://api.openai.com/v1")
LLM_MODEL = env_get("LLM_MODEL", "gpt-4o-mini")

# Optional GitHub mirror prefix, e.g. https://ghproxy.com for raw/clone URLs
GITHUB_PROXY = env_get("GITHUB_PROXY", "")


def proxied_github_url(url: str) -> str:
    """Return ``url`` optionally routed through ``GITHUB_PROXY``."""
    proxy = GITHUB_PROXY.strip()
    if not proxy:
        return url
    return f"{proxy.rstrip('/')}/{url}"


# GitHub tokens
GITHUB_TOKENS = env_get_list("GITHUB_TOKENS")


def resolve_path(path: str | Path, root: str | Path = PROJECT_ROOT) -> str:
    """Resolve relative path against configured project root."""
    root_path = Path(root)
    target_path = Path(path)
    if target_path.is_absolute():
        return str(target_path)
    return str(root_path / target_path)


def init_dir(paths: Iterable[str | Path] | None = None, root: str | Path = PROJECT_ROOT) -> list[str]:
    """Initialize directories and return [root, *resolved_paths]."""
    target_paths = tuple(paths) if paths is not None else BUILD_DOCKERFILE_LOG_DIRS
    resolved_paths = [resolve_path(path, root=root) for path in target_paths]
    for path in resolved_paths:
        os.makedirs(path, exist_ok=True)
        logger.debug(f"Ensured directory exists: {path}")
    return [resolve_path(".", root=root), *resolved_paths]


def as_str(path: Path) -> str:
    """Convert Path to string for legacy APIs."""
    return str(path)
