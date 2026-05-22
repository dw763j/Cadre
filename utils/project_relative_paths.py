"""Convert absolute paths in dataset JSON to paths relative to PROJECT_ROOT for portability."""
# python -m utils.project_relative_paths
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT, env_get_list

# Path fields used in structures such as dataset_valid_fixed_params_with_dockerfile.json
DEFAULT_DATASET_PATH_KEYS: frozenset[str] = frozenset(
    ("run_folder", "build_params_path", "error_log_path")
)

# Optional legacy absolute prefixes when migrating datasets from another machine (LEGACY_ABSOLUTE_ROOTS env, comma-separated)
DEFAULT_LEGACY_ABSOLUTE_ROOTS: tuple[str, ...] = tuple(env_get_list("LEGACY_ABSOLUTE_ROOTS"))


def to_relative_project_path(
    path: str | Path,
    *,
    root: Path | None = None,
    legacy_absolute_roots: Iterable[str] | None = None,
    strict: bool = False,
) -> str:
    """Convert an absolute path under ``root`` (default ``config.PROJECT_ROOT``) to a POSIX relative path string.

    First try ``Path.resolve()`` then relative to ``root``; if not under it, strip
    legacy absolute prefixes from ``legacy_absolute_roots`` (for migrating paths from old machines).
    If still unmappable and ``strict`` is true, raise ``ValueError``; otherwise return the string as-is.
    """
    if path is None:
        raise TypeError("path must not be None")
    root = (root or PROJECT_ROOT).resolve()
    legacy_roots = tuple(legacy_absolute_roots or DEFAULT_LEGACY_ABSOLUTE_ROOTS)
    raw = Path(path)
    try:
        resolved = raw.resolve()
        return resolved.relative_to(root).as_posix()
    except (ValueError, OSError):
        pass

    normalized = str(path).replace("\\", "/")
    for legacy in legacy_roots:
        leg = legacy.rstrip("/")
        if normalized == leg:
            return "."
        prefix = leg + "/"
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]

    if strict:
        raise ValueError(f"path is not under project root and has no known legacy prefix: {path!r}")
    return str(path)


def to_absolute_project_path(path: str | Path, *, root: Path | None = None) -> str:
    """Resolve a path relative to ``root`` to an absolute path string; if already absolute, return after ``resolve()``."""
    root = (root or PROJECT_ROOT).resolve()
    p = Path(path)
    if p.is_absolute():
        return str(p.resolve())
    return str((root / p).resolve())


def _walk_remap_path_keys(
    obj: Any,
    mapper: Callable[[str], str],
    keys: frozenset[str],
) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str):
                obj[k] = mapper(v)
            else:
                _walk_remap_path_keys(v, mapper, keys)
    elif isinstance(obj, list):
        for item in obj:
            _walk_remap_path_keys(item, mapper, keys)


def migrate_dataset_paths_to_relative(
    dataset: dict[str, Any] | list[Any] | Any,
    *,
    root: Path | None = None,
    legacy_absolute_roots: Iterable[str] | None = None,
    path_keys: frozenset[str] = DEFAULT_DATASET_PATH_KEYS,
    strict: bool = False,
) -> None:
    """Walk ``dataset`` in place (nested dict/list) and convert string fields in ``path_keys`` to relative paths."""
    root = root or PROJECT_ROOT

    def _map(p: str) -> str:
        return to_relative_project_path(
            p,
            root=root,
            legacy_absolute_roots=legacy_absolute_roots,
            strict=strict,
        )

    _walk_remap_path_keys(dataset, _map, path_keys)


def migrate_dataset_paths_to_absolute(
    dataset: dict[str, Any] | list[Any] | Any,
    *,
    root: Path | None = None,
    path_keys: frozenset[str] = DEFAULT_DATASET_PATH_KEYS,
) -> None:
    """Walk ``dataset`` in place and resolve string fields in ``path_keys`` to absolute paths under ``root``."""
    root = root or PROJECT_ROOT

    def _map(p: str) -> str:
        return to_absolute_project_path(p, root=root)

    _walk_remap_path_keys(dataset, _map, path_keys)


if __name__ == "__main__":
    # # from absolute to relative
    dataset = json.load(open("results/dataset_valid_fixed_params_with_dockerfile.json", encoding="utf-8"))
    migrate_dataset_paths_to_relative(dataset)
    json.dump(dataset, open("results/dataset_valid_fixed_params_with_dockerfile_relative.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    # from relative to local absolute
    dataset = json.load(open("results/dataset_valid_fixed_params_with_dockerfile_relative.json", encoding="utf-8"))
    migrate_dataset_paths_to_absolute(dataset)
    json.dump(dataset, open("results/dataset_valid_fixed_params_with_dockerfile_local_absolute.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
