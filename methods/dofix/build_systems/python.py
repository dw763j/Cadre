"""
Python build system detector with intelligent dependency analysis.
"""

import re
import os
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class PythonBuildSystemDetector(BuildSystemDetector):
    """Intelligent detector for Python build system commands."""
    
    @property
    def name(self) -> str:
        return "python"
    
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        if not command:
            return False
        
        # Match Python package/build tools and common subcommands
        # Poetry per docs: install, update, add, remove, lock, export, build, publish, run, check
        # uv per docs: run, sync, lock, add, remove, pip (install, download, wheel), venv, build, publish
        python_patterns = [
            r'^\s*(pip|python|python3)\s+(install|build|test|run|lint|format|check)\b',
            r'^\s*poetry\s+(install|update|add|remove|lock|export|build|publish|run|check)\b',
            r'^\s*uv\s+(run|sync|lock|add|remove|pip|venv|build|publish)\b',
            r'^\s*pipenv\s+(install|run|lock|update)\b',
            r'^\s*pytest\b',
            r'^\s*flake8\b',
            r'^\s*black\b',
            r'^\s*mypy\b'
        ]
        
        for pattern in python_patterns:
            if re.match(pattern, command, re.IGNORECASE):
                return True
        
        return False
    
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Intelligently analyze Python command and return files used from workspace."""
        command_parts = command.strip().split()
        if len(command_parts) < 2:
            raise ValueError(f"Invalid Python command: {command}")
        
        tool = command_parts[0].lower()
        subcommand = command_parts[1].lower()
        # Normalize uv "pip" subcommand into a composite like "uv pip"
        # so downstream branching can reason about it
        if tool == 'uv' and subcommand == 'pip' and len(command_parts) >= 3:
            subcommand = f"pip:{command_parts[2].lower()}"
        
        # Try intelligent analysis first
        try:
            result = self._intelligent_analysis(ctx, tool, subcommand)
            if result:
                return result
        except Exception as e:
            logger.debug(f"Intelligent analysis failed, falling back to basic: {e}")
        
        # Fallback to basic analysis
        return self._basic_analysis(ctx, subcommand)
    
    def _intelligent_analysis(self, ctx: BuildContext, tool: str, subcommand: str) -> BuildResult | None:
        """Intelligently analyze Python project structure and dependencies."""
        files = []
        globs = []
        
        # Find and parse Python project configuration files
        config_files = self._find_python_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Analyze build dependencies based on tool and subcommand
        if tool in ['pip', 'python', 'python3']:
            if subcommand == 'install':
                # For install, focus on dependency files and package configuration
                dep_files = self._find_dependency_files(ctx.workspace_path)
                files.extend(dep_files)
                
                # Analyze package configuration for install
                build_files = self._analyze_build_dependencies(ctx.workspace_path)
                files.extend(build_files)
                
            elif subcommand in ['build', 'test', 'run']:
                # For build/test/run, include source files and dependencies
                source_files = self._analyze_source_dependencies(ctx.workspace_path)
                files.extend(source_files)
                
                # Analyze build dependencies
                build_files = self._analyze_build_dependencies(ctx.workspace_path)
                files.extend(build_files)
                
        elif tool == 'poetry':
            # Poetry commands per docs
            if subcommand in ['install', 'update', 'add', 'remove', 'lock']:
                # dependency graph and lockfile relevant
                files.extend(self._find_dependency_files(ctx.workspace_path))
                files.extend(self._analyze_build_dependencies(ctx.workspace_path))
            if subcommand in ['build', 'publish', 'export', 'check', 'run', 'test']:
                files.extend(self._analyze_source_dependencies(ctx.workspace_path))
                files.extend(self._analyze_build_dependencies(ctx.workspace_path))
        elif tool == 'uv':
            # uv commands per docs
            if subcommand in ['install', 'sync', 'lock', 'add', 'remove']:
                files.extend(self._find_dependency_files(ctx.workspace_path))
                files.extend(self._analyze_build_dependencies(ctx.workspace_path))
            elif subcommand.startswith('pip:'):
                # uv pip install/download/wheel use requirements or pyproject
                files.extend(self._find_dependency_files(ctx.workspace_path))
            if subcommand in ['run', 'build', 'publish']:
                files.extend(self._analyze_source_dependencies(ctx.workspace_path))
                files.extend(self._analyze_build_dependencies(ctx.workspace_path))
                
        elif tool in ['pytest', 'flake8', 'black', 'mypy']:
            # Testing and linting tools
            test_files = self._find_test_files(ctx.workspace_path)
            files.extend(test_files)
            
            # Include source files for linting
            if tool in ['flake8', 'black', 'mypy']:
                source_files = self._analyze_source_dependencies(ctx.workspace_path)
                files.extend(source_files)
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.9
        )
    
    def _analyze_build_dependencies(self, workspace_path: str) -> list[str]:
        """Analyze files actually needed during the build."""
        files = []
        
        # 1. Parse package configuration in setup.py
        setup_py = os.path.join(workspace_path, 'setup.py')
        if os.path.isfile(setup_py):
            package_files = self._parse_setup_py_packages(setup_py, workspace_path)
            files.extend(package_files)
        
        # 2. Parse pyproject.toml
        pyproject_toml = os.path.join(workspace_path, 'pyproject.toml')
        if os.path.isfile(pyproject_toml):
            toml_files = self._parse_pyproject_toml(pyproject_toml, workspace_path)
            files.extend(toml_files)
        
        # 3. Analyze Python package import relationships
        import_files = self._analyze_python_imports(workspace_path)
        files.extend(import_files)
        
        # 4. Parse MANIFEST.in include rules
        manifest_in = os.path.join(workspace_path, 'MANIFEST.in')
        if os.path.isfile(manifest_in):
            manifest_files = self._parse_manifest_in(manifest_in, workspace_path)
            files.extend(manifest_files)
        
        return files
    
    def _parse_setup_py_packages(self, setup_py_path: str, workspace_path: str) -> list[str]:
        """Parse package configuration in setup.py."""
        files = []
        
        try:
            with open(setup_py_path) as f:
                content = f.read()
            
            # Find packages configuration
            packages_match = re.search(r'packages\s*=\s*\[(.*?)\]', content, re.DOTALL)
            if packages_match:
                packages_str = packages_match.group(1)
                packages = re.findall(r'["\']([^"\']+)["\']', packages_str)
                
                for package in packages:
                    package_path = os.path.join(workspace_path, package.replace('.', '/'))
                    if os.path.isdir(package_path):
                        files.append(package_path)
                        # Recursively add all Python files in the package
                        for root, _, filenames in os.walk(package_path):
                            for filename in filenames:
                                if filename.endswith('.py'):
                                    files.append(os.path.join(root, filename))
            
            # Find py_modules configuration
            py_modules_match = re.search(r'py_modules\s*=\s*\[(.*?)\]', content, re.DOTALL)
            if py_modules_match:
                modules_str = py_modules_match.group(1)
                modules = re.findall(r'["\']([^"\']+)["\']', modules_str)
                
                for module in modules:
                    module_file = os.path.join(workspace_path, f"{module}.py")
                    if os.path.isfile(module_file):
                        files.append(module_file)
        
        except Exception as e:
            logger.debug(f"Failed to parse setup.py: {e}")
        
        return files
    
    def _parse_pyproject_toml(self, toml_path: str, workspace_path: str) -> list[str]:
        """Parse package configuration in pyproject.toml."""
        files = []
        
        try:
            with open(toml_path) as f:
                content = f.read()
            
            # Record file itself
            files.append(toml_path)

            # Find [tool.poetry.packages] configuration
            packages_match = re.search(r'\[tool\.poetry\.packages\](.*?)(?=\[|$)', content, re.DOTALL)
            if packages_match:
                packages_section = packages_match.group(1)
                # Find include and from configuration
                include_match = re.search(r'include\s*=\s*["\']([^"\']+)["\']', packages_section)
                if include_match:
                    include_path = include_match.group(1)
                    full_path = os.path.join(workspace_path, include_path)
                    if os.path.isdir(full_path):
                        files.append(full_path)
                        # Recursively add all Python files in the directory
                        for root, _, filenames in os.walk(full_path):
                            for filename in filenames:
                                if filename.endswith('.py'):
                                    files.append(os.path.join(root, filename))

            # Parse requires-python from [project] or [tool.poetry.dependencies]
            # so the version files are considered
            requires_match = re.search(r'\[project\][\s\S]*?requires-python\s*=\s*["\']([^"\']+)["\']', content)
            if not requires_match:
                requires_match = re.search(r'\[tool\.poetry\.dependencies\][\s\S]*?python\s*=\s*["\']([^"\']+)["\']', content)
            if requires_match:
                version_files = ['.python-version', '.pyenv-version']
                for vf in version_files:
                    vp = os.path.join(workspace_path, vf)
                    if os.path.isfile(vp):
                        files.append(vp)
        
        except Exception as e:
            logger.debug(f"Failed to parse pyproject.toml: {e}")
        
        return files
    
    def _analyze_python_imports(self, workspace_path: str) -> list[str]:
        """Analyze import relationships across Python files and build a dependency graph."""
        files = []
        import_graph = {}
        
        # Scan all Python files
        for root, _, filenames in os.walk(workspace_path):
            for filename in filenames:
                if filename.endswith('.py'):
                    file_path = os.path.join(root, filename)
                    imports = self._extract_imports_from_file(file_path)
                    if imports:
                        import_graph[file_path] = imports
        
        # Starting from entry points, recursively find dependencies
        entry_points = self._find_python_entry_points(workspace_path)
        for entry_point in entry_points:
            dependent_files = self._find_dependent_files(entry_point, import_graph, workspace_path)
            files.extend(dependent_files)
        
        return list(set(files))
    
    def _extract_imports_from_file(self, file_path: str) -> list[str]:
        """Extract import statements from a Python file."""
        imports = []
        
        try:
            with open(file_path) as f:
                content = f.read()
            
            # Use regex to extract import statements
            import_patterns = [
                r'^import\s+([\w.]+)',  # import module
                r'^from\s+([\w.]+)\s+import',  # from module import
                r'^import\s+([\w.]+)\s+as',  # import module as
            ]
            
            for pattern in import_patterns:
                matches = re.findall(pattern, content, re.MULTILINE)
                imports.extend(matches)
        
        except Exception:
            pass
        
        return imports
    
    def _find_python_entry_points(self, workspace_path: str) -> list[str]:
        """Find entry points for a Python project."""
        entry_points = []
        
        # Find entry_points configuration in setup.py
        setup_py = os.path.join(workspace_path, 'setup.py')
        if os.path.isfile(setup_py):
            try:
                with open(setup_py) as f:
                    content = f.read()
                
                # Find console_scripts entry points
                console_scripts_match = re.search(r'console_scripts\s*=\s*\[(.*?)\]', content, re.DOTALL)
                if console_scripts_match:
                    scripts_section = console_scripts_match.group(1)
                    # Extract module paths
                    module_matches = re.findall(r'["\']([^=]+)=([^:]+):([^"\']+)["\']', scripts_section)
                    for _, module_path, _ in module_matches:
                        module_file = os.path.join(workspace_path, f"{module_path.replace('.', '/')}.py")
                        if os.path.isfile(module_file):
                            entry_points.append(module_file)
            except Exception:
                pass
        
        # Find main.py or app.py in the repo root
        for main_file in ['main.py', 'app.py', 'run.py']:
            main_path = os.path.join(workspace_path, main_file)
            if os.path.isfile(main_path):
                entry_points.append(main_path)
        
        return entry_points
    
    def _find_dependent_files(self, entry_point: str, import_graph: dict, workspace_path: str) -> list[str]:
        """Recursively find dependent files."""
        files = [entry_point]
        visited = set()
        
        def dfs(file_path: str):
            if file_path in visited:
                return
            visited.add(file_path)
            
            if file_path in import_graph:
                for import_name in import_graph[file_path]:
                    # Convert import name to file path
                    import_file = self._resolve_import_to_file(import_name, file_path, workspace_path)
                    if import_file and import_file not in visited:
                        files.append(import_file)
                        dfs(import_file)
        
        dfs(entry_point)
        return files
    
    def _resolve_import_to_file(self, import_name: str, source_file: str, workspace_path: str) -> str | None:
        """Resolve an import name to a file path."""
        # Handle relative imports
        if import_name.startswith('.'):
            source_dir = os.path.dirname(source_file)
            relative_path = import_name.replace('.', '/')
            if relative_path.startswith('/'):
                relative_path = relative_path[1:]
            
            # Look for __init__.py or .py files
            for ext in ['/__init__.py', '.py']:
                import_file = os.path.join(source_dir, relative_path + ext)
                if os.path.isfile(import_file):
                    return import_file
        
        # Handle absolute imports
        else:
            # Search for the module within the workspace
            for root, _, filenames in os.walk(workspace_path):
                for filename in filenames:
                    if filename.endswith('.py'):
                        if filename == '__init__.py':
                            # Check whether the directory matches the module name
                            dir_name = os.path.basename(root)
                            if dir_name == import_name.split('.')[-1]:
                                return os.path.join(root, filename)
                        else:
                            # Check whether the filename matches
                            module_name = filename[:-3]  # strip .py
                            if module_name == import_name.split('.')[-1]:
                                return os.path.join(root, filename)
        
        return None
    
    def _parse_manifest_in(self, manifest_path: str, workspace_path: str) -> list[str]:
        """Parse MANIFEST.in include rules."""
        files = []
        
        try:
            with open(manifest_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('include '):
                        pattern = line[8:].strip()
                        # Handle glob patterns
                        if '*' in pattern:
                            import glob
                            matches = glob.glob(os.path.join(workspace_path, pattern))
                            files.extend(matches)
                        else:
                            file_path = os.path.join(workspace_path, pattern)
                            if os.path.isfile(file_path):
                                files.append(file_path)
        
        except Exception as e:
            logger.debug(f"Failed to parse MANIFEST.in: {e}")
        
        return files
    
    def _find_python_configs(self, workspace_path: str) -> list[str]:
        """Find Python project configuration files."""
        config_files = []
        
        # Modern Python project files
        modern_configs = [
            'pyproject.toml', 'poetry.lock', 'uv.lock',
            'Pipfile', 'Pipfile.lock'
        ]
        
        for config in modern_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # Traditional Python project files
        traditional_configs = [
            'setup.py', 'setup.cfg', 'requirements.txt',
            'requirements-dev.txt', 'requirements-test.txt'
        ]
        
        for config in traditional_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # Python version files
        version_files = ['.python-version', '.pyenv-version']
        for version_file in version_files:
            version_path = os.path.join(workspace_path, version_file)
            if os.path.isfile(version_path):
                config_files.append(version_path)
        
        return config_files
    
    def _find_dependency_files(self, workspace_path: str) -> list[str]:
        """Find Python dependency specification files."""
        dep_files = []
        
        # Look for dependency files
        dep_patterns = [
            'requirements*.txt', 'Pipfile*', 'pyproject.toml',
            'poetry.lock', 'uv.lock'
        ]
        
        for pattern in dep_patterns:
            if '*' in pattern:
                # Handle glob patterns
                import glob
                matches = glob.glob(os.path.join(workspace_path, pattern))
                dep_files.extend(matches)
            else:
                file_path = os.path.join(workspace_path, pattern)
                if os.path.isfile(file_path):
                    dep_files.append(file_path)
        
        return dep_files
    
    def _analyze_source_dependencies(self, workspace_path: str) -> list[str]:
        """Analyze Python source file dependencies."""
        files = []
        
        # Common Python source directories
        source_dirs = ['src', 'app', 'main', 'core', 'api', 'services']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                files.append(source_path)
                # Also add Python files within these directories
                try:
                    for root, _dirs, filenames in os.walk(source_path):
                        for filename in filenames:
                            if filename.endswith('.py'):
                                file_path = os.path.join(root, filename)
                                if os.path.isfile(file_path):
                                    files.append(file_path)
                except (OSError, PermissionError):
                    pass
        
        # Add root Python files
        try:
            root_py_files = [f for f in os.listdir(workspace_path) 
                             if f.endswith('.py') and os.path.isfile(os.path.join(workspace_path, f))]
            for py_file in root_py_files:
                files.append(os.path.join(workspace_path, py_file))
        except (OSError, PermissionError):
            pass  # Skip if we can't list directory
        
        # Look for __init__.py files (package indicators)
        for root, _dirs, filenames in os.walk(workspace_path):
            if '__init__.py' in filenames:
                init_path = os.path.join(root, '__init__.py')
                if os.path.isfile(init_path):
                    files.append(init_path)
        
        return files
    
    def _find_test_files(self, workspace_path: str) -> list[str]:
        """Find Python test files and directories."""
        test_items = []
        
        # Common test directories
        test_dirs = ['test', 'tests', 'testing', 'spec']
        for test_dir in test_dirs:
            test_path = os.path.join(workspace_path, test_dir)
            if os.path.isdir(test_path):
                test_items.append(test_path)
        
        # Look for test files
        test_patterns = ['*_test.py', 'test_*.py', '*test.py']
        import glob
        for pattern in test_patterns:
            matches = glob.glob(os.path.join(workspace_path, '**', pattern), recursive=True)
            test_items.extend(matches)
        
        # Test configuration files
        test_configs = [
            'pytest.ini', 'pyproject.toml', '.coveragerc',
            'tox.ini', 'setup.cfg'
        ]
        for config in test_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                test_items.append(config_path)
        
        return test_items
    
    def _basic_analysis(self, ctx: BuildContext, subcommand: str) -> BuildResult:
        """Fallback to basic analysis when intelligent analysis fails."""
        files = []
        globs = []
        
        # Add common Python files
        config_files = self._find_python_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Add source file patterns
        if subcommand in ['build', 'test', 'run', 'lint', 'format']:
            globs.append(os.path.join(ctx.workspace_path, "**/*.py"))
            globs.append(os.path.join(ctx.workspace_path, "**/*.pyi"))  # Type stubs
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
