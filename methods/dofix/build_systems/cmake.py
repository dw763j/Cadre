"""
CMake build system detector with static CMakeLists.txt analysis.

Parses a subset of CMake commands to infer source and include files without
executing cmake or generators. Suitable for static build impact analysis.
"""

import os
import re
from collections import deque
from collections.abc import Iterable
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class CMakeBuildSystemDetector(BuildSystemDetector):
    @property
    def name(self) -> str:
        return "cmake"

    def matches(self, command: str) -> bool:
        if not command:
            return False
        return bool(re.match(r"^\s*cmake\s+(--build\b|[-A-DPSUBGRTc].*)", command))

    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        parts = command.strip().split()
        sub = "configure"
        target = None
        if "--build" in parts:
            sub = "build"
            if "--target" in parts:
                try:
                    target = parts[parts.index("--target") + 1]
                except Exception:
                    target = None
        command_type = sub if not target else f"build:{target}"

        files: list[str] = []
        globs: list[str] = []

        # Infer source dir
        src_dir = self._extract_arg_value(parts, "-S", ctx.current_workdir) or ctx.current_workdir or ctx.workspace_path
        src_dir = src_dir or ctx.workspace_path
        if not os.path.isabs(src_dir):
            src_dir = os.path.abspath(os.path.join(ctx.current_workdir or ctx.workspace_path, src_dir))

        # Collect CMakeLists.txt recursively via add_subdirectory
        cmakelists = self._collect_cmakelists(src_dir)
        if not cmakelists:
            # Fallback globs and top-level CMakeLists
            p = os.path.join(src_dir, "CMakeLists.txt")
            if os.path.isfile(p):
                files.append(p)
            self._fallback(src_dir, files, globs)
            return BuildResult(files=files, globs=globs, build_system=self.name, command_type=command_type, confidence=0.7)

        files.extend(cmakelists)

        # Parse cmake files to extract files and directories
        cmake_state = self._parse_cmake_files(cmakelists, src_dir)
        # If parser produced no targets, at least include CMakeLists
        if not cmake_state['target_to_files']:
            for c in cmakelists:
                if c not in files:
                    files.append(c)

        # Collect target-specific sources if requested
        if target and target in cmake_state['target_to_files']:
            files.extend(cmake_state['target_to_files'][target])
        else:
            for flist in cmake_state['target_to_files'].values():
                files.extend(flist)

        # Include directories -> add and glob headers
        for inc in cmake_state['include_dirs']:
            if os.path.isdir(inc):
                files.append(inc)
                globs.append(os.path.join(inc, "**/*.{h,hpp,hh}"))

        # file(GLOB...) patterns
        globs.extend(cmake_state['globs'])

        # One-level delegation: inspect custom commands for known build tools
        delegated = self._delegate_one_level(cmake_state.get('custom_commands', []), ctx)
        files.extend(delegated)

        if not cmake_state['target_to_files'] and not cmake_state['globs']:
            self._fallback(src_dir, files, globs)
            confidence = 0.75
        else:
            confidence = 0.9

        return BuildResult(files=self._uniq(files), globs=self._uniq(globs), build_system=self.name, command_type=command_type, confidence=confidence)

    def _extract_arg_value(self, parts: list[str], flag: str, base_dir: str | None = None) -> str | None:
        if flag in parts:
            try:
                val = parts[parts.index(flag) + 1]
                if base_dir and not os.path.isabs(val):
                    return os.path.abspath(os.path.join(base_dir, val))
                return os.path.abspath(val)
            except Exception:
                return None
        return None

    def _collect_cmakelists(self, src_dir: str) -> list[str]:
        found: list[str] = []
        root = os.path.join(src_dir, "CMakeLists.txt")
        if not os.path.isfile(root):
            return []
        # BFS via add_subdirectory
        q = deque([root])
        visited = set()
        while q:
            f = q.popleft()
            if f in visited:
                continue
            visited.add(f)
            found.append(f)
            base_dir = os.path.dirname(f)
            try:
                content = open(f, encoding='utf-8').read()
            except Exception as e:
                logger.debug(f"Failed to read {f}: {e}")
                continue
            for m in re.finditer(r"add_subdirectory\s*\(\s*([^\s)]+)", content, re.IGNORECASE):
                rel = m.group(1).strip().strip('"')
                sub = os.path.normpath(os.path.join(base_dir, rel))
                sub_cml = os.path.join(sub, "CMakeLists.txt")
                if os.path.isfile(sub_cml):
                    q.append(sub_cml)
        return found

    def _parse_cmake_files(self, files: list[str], src_dir: str) -> dict:
        target_to_files: dict[str, list[str]] = {}
        include_dirs: list[str] = []
        globs: list[str] = []
        varmap: dict[str, str] = {}
        custom_commands: list[str] = []

        def expand_vars(s: str) -> str:
            # Single pass ${VAR}
            return re.sub(r"\$\{([^}]+)\}", lambda m: varmap.get(m.group(1), ''), s)

        for f in files:
            base = os.path.dirname(f)
            try:
                content = open(f, encoding='utf-8').read()
            except Exception as e:
                logger.debug(f"Failed to read {f}: {e}")
                continue

            # set(VAR value)
            for m in re.finditer(r"set\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s+([^\)]+)\)", content, re.IGNORECASE):
                varmap[m.group(1)] = m.group(2).strip()

            # list(APPEND VAR ...)
            for m in re.finditer(r"list\s*\(\s*APPEND\s+([A-Za-z_][A-Za-z0-9_]*)\s+([^\)]+)\)", content, re.IGNORECASE):
                prev = varmap.get(m.group(1), '')
                varmap[m.group(1)] = (prev + ' ' + m.group(2).strip()).strip()

            # include directories
            for m in re.finditer(r"(include_directories|target_include_directories)\s*\(([^\)]+)\)", content, re.IGNORECASE):
                args = expand_vars(m.group(2))
                for word in self._split(args):
                    if word.upper() in {"PUBLIC", "PRIVATE", "INTERFACE"}:
                        continue
                    if word.startswith("$") or word.startswith("-D"):
                        continue
                    p = os.path.normpath(os.path.join(base, word))
                    include_dirs.append(p)

            # add_executable/add_library
            for pat in (r"add_executable\s*\(([^\)]+)\)", r"add_library\s*\(([^\)]+)\)"):
                for m in re.finditer(pat, content, re.IGNORECASE):
                    args = expand_vars(m.group(1))
                    words = self._split(args)
                    if not words:
                        continue
                    tgt = words[0]
                    files_acc: list[str] = target_to_files.setdefault(tgt, [])
                    for w in words[1:]:
                        if w.upper() in {"PUBLIC", "PRIVATE", "INTERFACE", "STATIC", "SHARED", "MODULE", "EXCLUDE_FROM_ALL"}:
                            continue
                        if w.startswith("$") or w.startswith("-D"):
                            continue
                        p = os.path.normpath(os.path.join(base, w))
                        if os.path.isfile(p) or os.path.isdir(p):
                            files_acc.append(p)
                        else:
                            # If looks like source with common ext, still include relative path best-effort
                            if any(w.endswith(ext) for ext in ('.c', '.cc', '.cxx', '.cpp', '.m', '.mm', '.h', '.hpp', '.hh')):
                                files_acc.append(p)

            # target_sources
            for m in re.finditer(r"target_sources\s*\(([^\)]+)\)", content, re.IGNORECASE):
                args = expand_vars(m.group(1))
                words = self._split(args)
                if not words:
                    continue
                tgt = words[0]
                files_acc: list[str] = target_to_files.setdefault(tgt, [])
                for w in words[1:]:
                    if w.upper() in {"PUBLIC", "PRIVATE", "INTERFACE"}:
                        continue
                    if w.startswith("$") or w.startswith("-D"):
                        continue
                    p = os.path.normpath(os.path.join(base, w))
                    if os.path.isfile(p) or os.path.isdir(p):
                        files_acc.append(p)
                    else:
                        if any(w.endswith(ext) for ext in ('.c', '.cc', '.cxx', '.cpp', '.m', '.mm', '.h', '.hpp', '.hh')):
                            files_acc.append(p)

            # file(GLOB ... PATTERNs)
            for m in re.finditer(r"file\s*\(\s*GLOB(_RECURSE)?\s+([^)]+)\)", content, re.IGNORECASE):
                args = expand_vars(m.group(2))
                # naive: collect words that look like patterns
                for w in self._split(args):
                    if any(ch in w for ch in ('*', '?', '[')):
                        p = os.path.normpath(os.path.join(base, w))
                        globs.append(p)

            # configure_file(IN OUT ...)
            for m in re.finditer(r"configure_file\s*\(\s*([^\s]+)\s+([^\s\)]+)", content, re.IGNORECASE):
                src = expand_vars(m.group(1))
                p = os.path.normpath(os.path.join(base, src))
                if os.path.isfile(p):
                    # Treat input template as source dependency
                    target_to_files.setdefault("__configure__", []).append(p)

            # add_custom_command / add_custom_target with COMMAND ...
            for m in re.finditer(r"add_custom_(command|target)\s*\(([^\)]+)\)", content, re.IGNORECASE):
                args = m.group(2)
                # naive parse: capture pieces after COMMAND
                for cm in re.finditer(r"COMMAND\s+([^;\)]+)", args, re.IGNORECASE):
                    cmd = expand_vars(cm.group(1)).strip()
                    if cmd:
                        custom_commands.append(cmd)

        return {
            'target_to_files': target_to_files,
            'include_dirs': include_dirs,
            'globs': globs,
            'custom_commands': custom_commands,
        }

    def _split(self, s: str) -> list[str]:
        # split on spaces while respecting simple quotes
        parts: list[str] = []
        cur = ''
        q = None
        for ch in s:
            if q:
                if ch == q:
                    q = None
                else:
                    cur += ch
            else:
                if ch in ('"', "'"):
                    q = ch
                elif ch.isspace():
                    if cur:
                        parts.append(cur)
                        cur = ''
                else:
                    cur += ch
        if cur:
            parts.append(cur)
        return parts

    def _fallback(self, src_dir: str, files: list[str], globs: list[str]) -> None:
        p = os.path.join(src_dir, "CMakeLists.txt")
        if os.path.isfile(p):
            files.append(p)
        globs.extend([
            os.path.join(src_dir, "src/**/*.{c,cc,cxx,cpp,h,hpp}"),
            os.path.join(src_dir, "include/**/*.{h,hpp}"),
            os.path.join(src_dir, "cmake/**/*.cmake"),
            os.path.join(src_dir, "*.cmake"),
        ])

    def _uniq(self, items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for it in items:
            if it not in seen:
                seen.add(it)
                out.append(it)
        return out

    def _delegate_one_level(self, commands: list[str], ctx: BuildContext) -> list[str]:
        if not commands:
            return []
        # Local import to avoid circular dependency at module import time
        from .manager import BuildSystemManager
        mgr = BuildSystemManager()
        out: list[str] = []
        seen: set[str] = set()
        for cmd in commands:
            s = cmd.strip()
            # limit to known tools to avoid running arbitrary commands
            if not re.match(r"^(go|cargo|npm|yarn|pnpm|node|npx|python|pip|mvn|gradle|dotnet|php|composer|ruby|bundle)\b", s):
                continue
            key = s.split(' ', 1)[0]
            if key in seen:
                continue
            seen.add(key)
            try:
                sub = mgr.analyze_command(s, ctx.workspace_path,ctx.current_workdir)
                if sub:
                    out.extend(sub.files)
            except Exception as e:
                logger.debug(f"Delegation for '{s}' failed: {e}")
        return out
