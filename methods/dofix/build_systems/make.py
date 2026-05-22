"""
Make build system detector with static Makefile analysis.

Parses Makefile includes, simple variables, and explicit rule dependencies
without executing make. Extracts likely file inputs for a target.
"""

import os
import re
from collections import deque
from collections.abc import Iterable
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class MakeBuildSystemDetector(BuildSystemDetector):
    @property
    def name(self) -> str:
        return "make"

    def matches(self, command: str) -> bool:
        if not command:
            return False
        return bool(re.match(r"^\s*make(\s+[-\w=./]+)*(\s+\w+)?\b", command))

    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        parts = command.strip().split()
        target = None
        # Rough parse: last token that is not an option or VAR=VAL is a target
        for token in parts[1:]:
            if token.startswith('-') or '=' in token:
                continue
            target = token
            break
        if target is None:
            target = "default"

        files: list[str] = []
        globs: list[str] = []

        # Discover makefiles (GNU make search order simplified)
        candidate_dirs = [ctx.current_workdir, ctx.workspace_path]
        seen = set()
        makefiles: list[str] = []
        for d in candidate_dirs:
            if not d:
                continue
            for name in ("GNUmakefile", "Makefile", "makefile"):
                p = os.path.join(d, name)
                if os.path.isfile(p) and p not in seen:
                    makefiles.append(p)
                    seen.add(p)
        if not makefiles:
            # Fallback
            for name in ("GNUmakefile", "Makefile", "makefile"):
                p = os.path.join(ctx.workspace_path, name)
                if os.path.isfile(p):
                    makefiles.append(p)
                    break

        if not makefiles:
            # No Makefile found: conservative globs
            self._fallback(ctx, files, globs)
            return BuildResult(files=files, globs=globs, build_system=self.name, command_type=target, confidence=0.7)

        files.extend(makefiles)

        # Parse graph
        graph, varmap, includes, explicit_file_deps = self._parse_makefiles(makefiles)
        files.extend(includes)
        files.extend(explicit_file_deps)

        # If no explicit graph, fallback to common patterns
        if not graph:
            self._fallback(ctx, files, globs)
            return BuildResult(files=self._uniq(files), globs=self._uniq(globs), build_system=self.name, command_type=target, confidence=0.75)

        # Resolve target closure
        deps_files = self._resolve_target_dependencies(graph, varmap, target, base_dir=os.path.dirname(makefiles[0]))
        files.extend(deps_files)

        # Heuristics from recipe commands (-I, -include, scripts, etc.)
        recipe_refs = self._extract_recipe_references(makefiles, varmap)
        files.extend(recipe_refs[0])
        globs.extend(recipe_refs[1])

        # One-level delegation to other build systems found in recipes
        delegated_files = self._delegate_one_level_from_recipes(makefiles, ctx)
        files.extend(delegated_files)

        if not deps_files and not recipe_refs[0] and not recipe_refs[1]:
            # Still add common src/include patterns as weak signal
            globs.extend(self._common_globs(ctx.workspace_path))
            confidence = 0.75
        else:
            confidence = 0.9

        return BuildResult(files=self._uniq(files), globs=self._uniq(globs), build_system=self.name, command_type=target, confidence=confidence)

    def _parse_makefiles(self, makefiles: list[str]) -> tuple[dict[str, list[str]], dict[str, str], list[str], list[str]]:
        graph: dict[str, list[str]] = {}
        varmap: dict[str, str] = {}
        includes: list[str] = []
        explicit_file_deps: list[str] = []

        to_visit = deque(makefiles)
        visited = set()
        depth = 0
        max_depth = 8
        while to_visit and depth < max_depth:
            depth += 1
            mf = to_visit.popleft()
            if mf in visited:
                continue
            visited.add(mf)
            base_dir = os.path.dirname(mf)
            try:
                content = open(mf, encoding='utf-8').read()
            except Exception as e:
                logger.debug(f"Failed to read {mf}: {e}")
                continue

            # variable assignments (simple)
            for m in re.finditer(r"^(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*([:+?]?=)\s*(?P<val>.*)$", content, re.MULTILINE):
                varmap[m.group('var')] = m.group('val').strip()

            # includes
            for m in re.finditer(r"^(-?include)\s+(?P<inc>[^\n#]+)$", content, re.MULTILINE):
                paths = self._split_words(m.group('inc'))
                for rel in paths:
                    rel = self._expand_vars(rel, varmap)
                    inc_path = os.path.normpath(os.path.join(base_dir, rel))
                    if os.path.isfile(inc_path):
                        includes.append(inc_path)
                        to_visit.append(inc_path)

            # rules: target: deps (strict) - disallow newlines in target/deps
            for m in re.finditer(r"^(?P<t>[^\s:\n][^:\n]*):\s*(?P<deps>[^\n#]*)$", content, re.MULTILINE):
                t = m.group('t').strip()
                # Normalize target to last line in case of accidental capture
                t = t.splitlines()[-1].strip()
                deps = [self._expand_vars(x, varmap) for x in self._split_words(m.group('deps'))]
                if t not in graph:
                    graph[t] = []
                graph[t].extend(d for d in deps if d)
                # Collect explicit file-looking deps that exist
                for d in deps:
                    if any(ch in d for ch in ('/', '.')):
                        p = os.path.normpath(os.path.join(base_dir, d))
                        if os.path.isfile(p) or os.path.isdir(p):
                            explicit_file_deps.append(p)

            # If no rules captured (some Makefiles contain trailing comments etc.), try a looser pattern
            if not graph:
                for m in re.finditer(r"^(?P<t>[A-Za-z0-9_./%+-]+)\s*:\s*(?P<deps>[^\n#]+)", content, re.MULTILINE):
                    t = m.group('t').strip()
                    deps = [self._expand_vars(x, varmap) for x in self._split_words(m.group('deps'))]
                    if t not in graph:
                        graph[t] = []
                    graph[t].extend(d for d in deps if d)
                    for d in deps:
                        if any(ch in d for ch in ('/', '.')):
                            p = os.path.normpath(os.path.join(base_dir, d))
                            if os.path.isfile(p) or os.path.isdir(p):
                                explicit_file_deps.append(p)

        return graph, varmap, includes, explicit_file_deps

    def _split_words(self, s: str) -> list[str]:
        return [w for w in re.split(r"\s+", s.strip()) if w]

    def _expand_vars(self, s: str, varmap: dict[str, str]) -> str:
        def repl(m: re.Match) -> str:
            key = m.group(1) or m.group(2)
            return varmap.get(key, '')
        return re.sub(r"\$\(([^)]+)\)|\${([^}]+)}", repl, s)

    def _resolve_target_dependencies(self, graph: dict[str, list[str]], varmap: dict[str, str], target: str, base_dir: str) -> list[str]:
        # choose first target if default
        if target == "default" and graph:
            target = next(iter(graph.keys()))
        visited: set[str] = set()
        stack = [target]
        files: list[str] = []
        steps = 0
        while stack and steps < 10000:
            steps += 1
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            for dep in graph.get(cur, []):
                if dep in visited:
                    continue
                stack.append(dep)
                # If looks like a file path
                if any(ch in dep for ch in ("/", ".")) and not dep.strip().startswith("$"):
                    p = os.path.normpath(os.path.join(base_dir, dep))
                    if os.path.isfile(p) or os.path.isdir(p):
                        files.append(p)
        return files

    def _extract_recipe_references(self, makefiles: list[str], varmap: dict[str, str]) -> tuple[list[str], list[str]]:
        files: list[str] = []
        globs: list[str] = []
        flag_patterns = [
            r"-I\s*([^\s]+)",
            r"-isystem\s*([^\s]+)",
            r"-include\s*([^\s]+)",
            r"@\s*sh\s+([^\s]+)",
            r"\./[^\s]+\.(sh|py)",
        ]
        self._last_recipe_lines: list[str] = []
        for mf in makefiles:
            base_dir = os.path.dirname(mf)
            try:
                content = open(mf, encoding='utf-8').read()
            except Exception:
                continue
            # Find recipe lines (lines starting with tab or with command separators)
            for line in content.splitlines():
                if not line or (not line.startswith('\t') and not line.lstrip().startswith('@') and not line.lstrip().startswith('-')):
                    continue
                self._last_recipe_lines.append(line.strip())
                for pat in flag_patterns:
                    for m in re.finditer(pat, line):
                        ref = self._expand_vars(m.group(1) if m.groups() else m.group(0), varmap)
                        p = os.path.normpath(os.path.join(base_dir, ref))
                        if os.path.isdir(p):
                            files.append(p)
                            globs.append(os.path.join(p, "**/*"))
                        elif os.path.isfile(p):
                            files.append(p)
        return files, globs

    def _delegate_one_level_from_recipes(self, makefiles: list[str], ctx: BuildContext) -> list[str]:
        # Avoid recursion by only doing one level per analyze call
        if not hasattr(self, '_last_recipe_lines'):
            return []
        commands: list[str] = []
        for line in self._last_recipe_lines:
            # Strip leading control chars like '@' or '-'
            s = line.lstrip('@-').strip()
            # collect potential build tool invocations
            if re.match(r"^(go|cargo|npm|yarn|pnpm|node|npx|python|pip|mvn|gradle|dotnet|php|composer|ruby|bundle)\b", s):
                commands.append(s)
        if not commands:
            return []
        # Local import to avoid circular dependency at module import time
        from .manager import BuildSystemManager
        mgr = BuildSystemManager()
        delegated_files: list[str] = []
        seen_cmds: set[str] = set()
        for cmd in commands:
            # de-duplicate similar commands
            key = cmd.split(' ', 1)[0]
            if key in seen_cmds:
                continue
            seen_cmds.add(key)
            try:
                sub = mgr.analyze_command(cmd, ctx.workspace_path, ctx.current_workdir)
                if sub:
                    delegated_files.extend(sub.files)
            except Exception as e:
                logger.debug(f"Delegation for '{cmd}' failed: {e}")
        return delegated_files

    def _common_globs(self, root: str) -> list[str]:
        patterns = [
            os.path.join(root, "src/**/*.{c,cc,cxx,cpp,h,hpp}"),
            os.path.join(root, "include/**/*.{h,hpp}"),
            os.path.join(root, "scripts/**/*"),
            os.path.join(root, "*.mk"),
        ]
        return patterns

    def _fallback(self, ctx: BuildContext, files: list[str], globs: list[str]) -> None:
        for name in ("GNUmakefile", "Makefile", "makefile"):
            p = os.path.join(ctx.workspace_path, name)
            if os.path.isfile(p):
                files.append(p)
        globs.extend(self._common_globs(ctx.workspace_path))

    def _uniq(self, items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for it in items:
            if it not in seen:
                seen.add(it)
                out.append(it)
        return out
