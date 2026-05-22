"""
Helpers for integrating with local Go tooling to enumerate precise inputs.

This module optionally invokes `go list -json -deps` to obtain per-package
file lists (GoFiles, CgoFiles, TestGoFiles, XTestGoFiles, EmbedFiles, etc.).
It strictly operates on the local workspace and never accesses the container.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
from loguru import logger


class GoToolingUnavailable(Exception):
    pass


class GoTooling:
    """Thin wrapper around local `go` tool to query package file lists."""

    def __init__(self, workspace_path: str, env: dict[str, str] | None = None):
        self.workspace_path = workspace_path
        self.env = (env or {}).copy()
        self._check_availability()

    def _check_availability(self) -> None:
        if not shutil.which("go"):
            raise GoToolingUnavailable("`go` executable not found in PATH")
        if not os.path.isdir(self.workspace_path):
            raise GoToolingUnavailable("workspace_path is not a directory")
        if not os.path.isfile(os.path.join(self.workspace_path, "go.mod")):
            raise GoToolingUnavailable("go.mod not found in workspace")

    def list_package_files(self, pattern: str = "./...") -> list[str]:
        """
        Run `go list -json -deps <pattern>` and collect input files under workspace.
        Returns absolute paths within the workspace.
        """
        cmd = ["go", "list", "-json", "-deps", pattern]
        env = os.environ.copy()
        env.update(self.env)

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.workspace_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as e:
            raise GoToolingUnavailable(f"Failed to execute go list: {e}")

        if proc.returncode != 0:
            logger.debug(f"go list failed: rc={proc.returncode}, stderr={proc.stderr.strip()[:200]}")
            raise GoToolingUnavailable("go list returned non-zero exit code")

        return self._parse_go_list_json_stream(proc.stdout)

    def _parse_go_list_json_stream(self, data: str) -> list[str]:
        """
        Parse concatenated JSON objects from `go list -json` output.
        Collect file lists for each package and return unique absolute paths
        that are within the workspace directory.
        """
        files: set[str] = set()
        buf = []
        depth = 0
        for ch in data:
            if ch == '{':
                depth += 1
            if depth > 0:
                buf.append(ch)
            if ch == '}':
                depth -= 1
                if depth == 0 and buf:
                    obj_str = ''.join(buf)
                    buf = []
                    try:
                        pkg = json.loads(obj_str)
                        self._collect_pkg_files(pkg, files)
                    except Exception as e:
                        logger.debug(f"Failed to parse go list object: {e}")
                        continue
        final_files = sorted(set([file for file in files if os.path.exists(file)]))
        return final_files

    def _collect_pkg_files(self, pkg: dict, files: set[str]) -> None:
        dir_path = pkg.get("Dir")
        if not self._is_in_workspace(dir_path):
            return

        if not isinstance(dir_path, str) or not dir_path:
            return

        def add_paths(field: str) -> None:
            arr = pkg.get(field)
            if isinstance(arr, list):
                for name in arr:
                    if isinstance(name, str):
                        abs_path = os.path.normpath(os.path.join(dir_path, name))
                        if self._is_in_workspace(abs_path):
                            files.add(abs_path)

        # Common fields containing file lists
        for f in (
            "GoFiles",
            "CgoFiles",
            # "IgnoredGoFiles",
            "TestGoFiles",
            "XTestGoFiles",
            "EmbedFiles",
            "Imports",
        ):
            add_paths(f)
        # Also include go.mod and go.sum
        for extra in ("go.mod", "go.sum"):
            p = os.path.join(self.workspace_path, extra)
            if os.path.isfile(p):
                files.add(os.path.normpath(p))

    def _is_in_workspace(self, path: str) -> bool:
        try:
            ws = os.path.abspath(self.workspace_path)
            ap = os.path.abspath(path)
            return ap == ws or ap.startswith(ws + os.sep)
        except Exception:
            return False
