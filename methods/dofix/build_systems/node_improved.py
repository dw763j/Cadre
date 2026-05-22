"""
Improved Node.js build system detector with source-level dependency analysis.

This implementation focuses on analyzing actual source code dependencies
without requiring external packages, making it suitable for environments
where only standard language runtimes are available.
"""

import re
import os
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class NodeDependencyAnalyzer:
    """Analyzes Node.js source code dependencies without external packages."""
    
    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.dependency_graph: dict[str, set[str]] = {}
        self.file_dependencies: dict[str, set[str]] = {}
        
    def analyze_dependencies(self, entry_files: list[str]) -> set[str]:
        """Analyze dependencies starting from entry files."""
        all_dependencies = set()
        visited = set()
        
        for entry_file in entry_files:
            if entry_file not in visited:
                self._analyze_file_dependencies(entry_file, visited, all_dependencies)
        
        return all_dependencies
    
    def _analyze_file_dependencies(self, file_path: str, visited: set[str], 
                                 all_dependencies: set[str]) -> None:
        """Recursively analyze file dependencies."""
        if file_path in visited:
            return
        
        visited.add(file_path)
        all_dependencies.add(file_path)
        
        # Get dependencies for this file
        dependencies = self._extract_file_dependencies(file_path)
        
        for dep in dependencies:
            if dep not in visited:
                self._analyze_file_dependencies(dep, visited, all_dependencies)
    
    def _extract_file_dependencies(self, file_path: str) -> set[str]:
        """Extract dependencies from a single file."""
        if not os.path.isfile(file_path):
            return set()
        
        dependencies = set()
        
        try:
            with open(file_path, encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.debug(f"Failed to read file {file_path}: {e}")
            return dependencies
        
        # Extract different types of dependencies based on file extension
        if file_path.endswith('.js') or file_path.endswith('.jsx'):
            dependencies.update(self._extract_js_dependencies(content, file_path))
        elif file_path.endswith('.ts') or file_path.endswith('.tsx'):
            dependencies.update(self._extract_ts_dependencies(content, file_path))
        elif file_path.endswith('.json'):
            dependencies.update(self._extract_json_dependencies(content, file_path))
        elif file_path.endswith('.js') and 'webpack' in file_path.lower():
            dependencies.update(self._extract_webpack_dependencies(content, file_path))
        elif file_path.endswith('.js') and 'vite' in file_path.lower():
            dependencies.update(self._extract_vite_dependencies(content, file_path))
        
        return dependencies
    
    def _extract_js_dependencies(self, content: str, file_path: str) -> set[str]:
        """Extract dependencies from JavaScript files."""
        dependencies = set()
        
        # CommonJS require statements
        require_pattern = r'require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)'
        for match in re.finditer(require_pattern, content):
            module_path = match.group(1)
            resolved_path = self._resolve_module_path(module_path, file_path)
            if resolved_path:
                dependencies.add(resolved_path)
        
        # ES6 import statements
        import_patterns = [
            r'import\s+.*?from\s+[\'"]([^\'"]+)[\'"]',
            r'import\s+[\'"]([^\'"]+)[\'"]',
            r'export\s+.*?from\s+[\'"]([^\'"]+)[\'"]'
        ]
        
        for pattern in import_patterns:
            for match in re.finditer(pattern, content):
                module_path = match.group(1)
                resolved_path = self._resolve_module_path(module_path, file_path)
                if resolved_path:
                    dependencies.add(resolved_path)
        
        return dependencies
    
    def _extract_ts_dependencies(self, content: str, file_path: str) -> set[str]:
        """Extract dependencies from TypeScript files."""
        # TypeScript has the same import/export syntax as ES6
        return self._extract_js_dependencies(content, file_path)
    
    def _extract_json_dependencies(self, content: str, file_path: str) -> set[str]:
        """Extract dependencies from JSON files."""
        dependencies = set()
        
        try:
            # Simple JSON parsing without external libraries
            # Look for file paths in common fields
            file_path_patterns = [
                r'[\'"](main|module|types|exports|bin)[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]',
                r'[\'"](entry|input|output)[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]',
                r'[\'"](src|lib|dist|build)[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]'
            ]
            
            for pattern in file_path_patterns:
                for match in re.finditer(pattern, content):
                    # field_name = match.group(1)
                    field_value = match.group(2)
                    
                    if not field_value.startswith('http') and not field_value.startswith('npm:'):
                        resolved_path = self._resolve_module_path(field_value, file_path)
                        if resolved_path:
                            dependencies.add(resolved_path)
        except Exception as e:
            logger.debug(f"Failed to parse JSON dependencies in {file_path}: {e}")
        
        return dependencies
    
    def _extract_webpack_dependencies(self, content: str, file_path: str) -> set[str]:
        """Extract dependencies from webpack configuration files."""
        dependencies = set()
        
        # Look for entry points, output paths, and other file references
        webpack_patterns = [
            r'entry\s*:\s*[\'"]([^\'"]+)[\'"]',
            r'entry\s*:\s*\[\s*[\'"]([^\'"]+)[\'"]',
            r'output\s*:\s*{[^}]*path\s*:\s*[\'"]([^\'"]+)[\'"]',
            r'include\s*:\s*[\'"]([^\'"]+)[\'"]',
            r'exclude\s*:\s*[\'"]([^\'"]+)[\'"]'
        ]
        
        for pattern in webpack_patterns:
            for match in re.finditer(pattern, content):
                file_ref = match.group(1)
                resolved_path = self._resolve_module_path(file_ref, file_path)
                if resolved_path:
                    dependencies.add(resolved_path)
        
        return dependencies
    
    def _extract_vite_dependencies(self, content: str, file_path: str) -> set[str]:
        """Extract dependencies from Vite configuration files."""
        dependencies = set()
        
        # Look for Vite-specific configurations
        vite_patterns = [
            r'root\s*:\s*[\'"]([^\'"]+)[\'"]',
            r'build\s*:\s*{[^}]*outDir\s*:\s*[\'"]([^\'"]+)[\'"]',
            r'resolve\s*:\s*{[^}]*alias\s*:\s*{[^}]*[\'"]([^\'"]+)[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]'
        ]
        
        for pattern in vite_patterns:
            for match in re.finditer(pattern, content):
                if len(match.groups()) == 1:
                    file_ref = match.group(1)
                else:
                    file_ref = match.group(2)  # For alias patterns
                
                resolved_path = self._resolve_module_path(file_ref, file_path)
                if resolved_path:
                    dependencies.add(resolved_path)
        
        return dependencies
    
    def _resolve_module_path(self, module_path: str, from_file: str) -> str | None:
        """Resolve a module path to an absolute file path."""
        if not module_path or module_path.startswith('http'):
            return None
        
        # Handle relative paths
        if module_path.startswith('.'):
            from_dir = os.path.dirname(from_file)
            resolved_path = os.path.normpath(os.path.join(from_dir, module_path))
        else:
            # Handle absolute paths from workspace root
            resolved_path = os.path.normpath(os.path.join(self.workspace_path, module_path))
        
        # Try different file extensions
        extensions = ['', '.js', '.jsx', '.ts', '.tsx', '.json']
        for ext in extensions:
            test_path = resolved_path + ext
            if os.path.isfile(test_path):
                return test_path
        
        # Check if it's a directory with index file
        if os.path.isdir(resolved_path):
            for ext in extensions:
                index_path = os.path.join(resolved_path, 'index' + ext)
                if os.path.isfile(index_path):
                    return index_path
        
        return None


class ImprovedNodeBuildSystemDetector(BuildSystemDetector):
    """Improved Node.js build system detector with source-level dependency analysis."""
    
    @property
    def name(self) -> str:
        return "node_improved"
    
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        if not command:
            return False
        
        # Match Node.js package manager commands
        node_patterns = [
            r'^\s*(npm|yarn|pnpm)\s+(install|ci|build|run|start|test|lint|format)\b',
            r'^\s*(npm|yarn|pnpm)\s+run\s+\w+',
            r'^\s*(npm|yarn|pnpm)\s+exec\s+\w+',
            r'^\s*node\s+\w+',
            r'^\s*npx\s+\w+'
        ]
        
        for pattern in node_patterns:
            if re.match(pattern, command, re.IGNORECASE):
                return True
        
        return False
    
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Analyze Node.js command and return files used from workspace."""
        command_parts = command.strip().split()
        if len(command_parts) < 2:
            raise ValueError(f"Invalid Node.js command: {command}")
        
        package_manager = command_parts[0].lower()
        subcommand = command_parts[1].lower()
        
        # Try intelligent analysis first
        try:
            result = self._intelligent_analysis(command, ctx, package_manager, subcommand)
            if result:
                return result
        except Exception as e:
            logger.debug(f"Intelligent analysis failed, falling back to basic: {e}")
        
        # Fallback to basic analysis
        return self._basic_analysis(ctx, subcommand)
    
    def _intelligent_analysis(self, command: str, ctx: BuildContext, 
                            package_manager: str, subcommand: str) -> BuildResult | None:
        """Intelligently analyze package.json and build entry points."""
        package_json_path = os.path.join(ctx.workspace_path, "package.json")
        if not os.path.isfile(package_json_path):
            return None
        
        try:
            package_data = self._parse_package_json(package_json_path)
        except Exception as e:
            logger.debug(f"Failed to parse package.json: {e}")
            return None
        
        files = [package_json_path]
        
        # Add lock files
        lock_files = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]
        for lock_file in lock_files:
            lock_path = os.path.join(ctx.workspace_path, lock_file)
            if os.path.isfile(lock_path):
                files.append(lock_path)
        
        # Analyze based on subcommand
        if subcommand in ['build', 'run']:
            # Extract script name for 'run' command
            script_name = self._extract_script_name(command)
            if script_name:
                script_files = self._analyze_script_dependencies(
                    package_data, script_name, ctx.workspace_path
                )
                files.extend(script_files)
            
            # Add build configuration files
            build_configs = self._find_build_configs(ctx.workspace_path)
            files.extend(build_configs)
            
            # Analyze source dependencies using dependency analyzer
            entry_files = self._get_entry_files(package_data, ctx.workspace_path)
            if entry_files:
                analyzer = NodeDependencyAnalyzer(ctx.workspace_path)
                source_dependencies = analyzer.analyze_dependencies(entry_files)
                files.extend(list(source_dependencies))
            
        elif subcommand in ['test', 'lint', 'format']:
            # Add test/lint specific files
            test_files = self._find_test_files(ctx.workspace_path)
            files.extend(test_files)
            
            # Add configuration files
            config_files = self._find_config_files(ctx.workspace_path)
            files.extend(config_files)
            
        elif subcommand in ['install', 'ci']:
            # For install, we mainly need package files and workspace configs
            workspace_configs = self._find_workspace_configs(ctx.workspace_path)
            files.extend(workspace_configs)
        
        return BuildResult(
            files=files,
            globs=[],
            build_system=self.name,
            command_type=subcommand,
            confidence=0.95
        )
    
    def _parse_package_json(self, package_json_path: str) -> dict:
        """Parse package.json without external dependencies."""
        try:
            with open(package_json_path, encoding='utf-8') as f:
                content = f.read()
            
            # Simple JSON parsing for common fields
            package_data = {}
            
            # Extract main entry point
            main_match = re.search(r'[\'"](main)[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]', content)
            if main_match:
                package_data['main'] = main_match.group(2)
            
            # Extract scripts
            scripts_match = re.search(r'[\'"](scripts)[\'"]\s*:\s*({[^}]+})', content)
            if scripts_match:
                scripts_content = scripts_match.group(2)
                scripts = {}
                script_matches = re.finditer(r'[\'"]([^\'"]+)[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]', scripts_content)
                for match in script_matches:
                    scripts[match.group(1)] = match.group(2)
                package_data['scripts'] = scripts
            
            # Extract other entry points
            for field in ['module', 'types', 'bin']:
                field_match = re.search(f'[\'"]({field})[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]', content)
                if field_match:
                    package_data[field] = field_match.group(2)
            
            return package_data
            
        except Exception as e:
            logger.debug(f"Failed to parse package.json: {e}")
            return {}
    
    def _extract_script_name(self, command: str) -> str | None:
        """Extract script name from npm/yarn run command."""
        parts = command.split()
        for i, part in enumerate(parts):
            if part.lower() in ['run']:
                if i + 1 < len(parts) and not parts[i + 1].startswith('-'):
                    return parts[i + 1]
        return None
    
    def _analyze_script_dependencies(self, package_data: dict, script_name: str, 
                                   workspace_path: str) -> list[str]:
        """Analyze dependencies for a specific script."""
        files = []
        
        # Get script content
        scripts = package_data.get('scripts', {})
        script_content = scripts.get(script_name, '')
        
        if not script_content:
            return files
        
        # Look for common build tools in script content
        build_tools = {
            'webpack': ['webpack.config.js', 'webpack.config.ts'],
            'vite': ['vite.config.js', 'vite.config.ts'],
            'rollup': ['rollup.config.js', 'rollup.config.ts'],
            'babel': ['babel.config.js', 'babel.config.json', '.babelrc'],
            'typescript': ['tsconfig.json', 'tsconfig.build.json'],
            'eslint': ['.eslintrc.js', '.eslintrc.json', '.eslintrc'],
            'prettier': ['.prettierrc', '.prettierrc.json', '.prettierrc.js']
        }
        
        for tool, config_files in build_tools.items():
            if tool in script_content.lower():
                for config_file in config_files:
                    config_path = os.path.join(workspace_path, config_file)
                    if os.path.isfile(config_path):
                        files.append(config_path)
        
        return files
    
    def _get_entry_files(self, package_data: dict, workspace_path: str) -> list[str]:
        """Get entry files from package.json."""
        entry_files = []
        
        # Check main entry point
        main_entry = package_data.get('main')
        if main_entry:
            main_path = os.path.join(workspace_path, main_entry)
            if os.path.isfile(main_path):
                entry_files.append(main_path)
        
        # Check other entry points
        for field in ['module', 'types', 'bin']:
            entry = package_data.get(field)
            if entry:
                entry_path = os.path.join(workspace_path, entry)
                if os.path.isfile(entry_path):
                    entry_files.append(entry_path)
        
        # If no entry points found, look for common entry files
        if not entry_files:
            common_entries = ['index.js', 'index.ts', 'main.js', 'main.ts', 'app.js', 'app.ts']
            for entry in common_entries:
                entry_path = os.path.join(workspace_path, entry)
                if os.path.isfile(entry_path):
                    entry_files.append(entry_path)
                    break
        
        return entry_files
    
    def _find_build_configs(self, workspace_path: str) -> list[str]:
        """Find build configuration files."""
        config_files = [
            'tsconfig.json', 'webpack.config.js', 'vite.config.js',
            'rollup.config.js', 'babel.config.js', '.babelrc',
            'esbuild.config.js', 'parcel.config.js'
        ]
        
        found_configs = []
        for config_file in config_files:
            config_path = os.path.join(workspace_path, config_file)
            if os.path.isfile(config_path):
                found_configs.append(config_path)
        
        return found_configs
    
    def _find_test_files(self, workspace_path: str) -> list[str]:
        """Find test-related files and directories."""
        test_items = []
        
        # Common test directories
        test_dirs = ['test', 'tests', '__tests__', 'spec', 'specs']
        for test_dir in test_dirs:
            test_path = os.path.join(workspace_path, test_dir)
            if os.path.isdir(test_path):
                test_items.append(test_path)
        
        # Test configuration files
        test_configs = [
            'jest.config.js', 'jest.config.ts', 'mocha.opts',
            'cypress.config.js', 'cypress.config.ts'
        ]
        for config in test_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                test_items.append(config_path)
        
        return test_items
    
    def _find_config_files(self, workspace_path: str) -> list[str]:
        """Find configuration files."""
        config_files = [
            '.eslintrc.js', '.eslintrc.json', '.eslintrc',
            '.prettierrc', '.prettierrc.json', '.prettierrc.js',
            '.browserslistrc', '.nvmrc', '.node-version'
        ]
        
        found_configs = []
        for config_file in config_files:
            config_path = os.path.join(workspace_path, config_file)
            if os.path.isfile(config_path):
                found_configs.append(config_path)
        
        return found_configs
    
    def _find_workspace_configs(self, workspace_path: str) -> list[str]:
        """Find workspace configuration files."""
        workspace_configs = ['lerna.json', 'nx.json', 'rush.json', 'pnpm-workspace.yaml']
        
        found_configs = []
        for config in workspace_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                found_configs.append(config_path)
        
        return found_configs
    
    def _basic_analysis(self, ctx: BuildContext, subcommand: str) -> BuildResult:
        """Fallback to basic analysis when intelligent analysis fails."""
        files = []
        
        # Always include package.json
        package_json = os.path.join(ctx.workspace_path, "package.json")
        if os.path.isfile(package_json):
            files.append(package_json)
        
        # Add lock files
        lock_files = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]
        for lock_file in lock_files:
            lock_path = os.path.join(ctx.workspace_path, lock_file)
            if os.path.isfile(lock_path):
                files.append(lock_path)
        
        # Add configuration files
        config_files = [
            'tsconfig.json', 'webpack.config.js', 'vite.config.js',
            'rollup.config.js', 'babel.config.js', '.eslintrc.js',
            '.eslintrc.json', '.prettierrc', '.prettierrc.json'
        ]
        for config_file in config_files:
            config_path = os.path.join(ctx.workspace_path, config_file)
            if os.path.isfile(config_path):
                files.append(config_path)
        
        return BuildResult(
            files=files,
            globs=[],
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
