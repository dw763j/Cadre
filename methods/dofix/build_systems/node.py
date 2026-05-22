"""
Node.js build system detector with intelligent dependency analysis.
"""

import re
import os
import json
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class NodeBuildSystemDetector(BuildSystemDetector):
    """Intelligent detector for Node.js build system commands."""
    
    @property
    def name(self) -> str:
        return "node"
    
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
        """Intelligently analyze Node.js command and return files used from workspace."""
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
            with open(package_json_path) as f:
                package_data = json.load(f)
        except Exception as e:
            logger.debug(f"Failed to parse package.json: {e}")
            return None
        
        files = [package_json_path]
        globs = []
        
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
            
            # Add source files based on entry points
            source_files = self._analyze_source_dependencies(package_data, ctx.workspace_path)
            files.extend(source_files)
            
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
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.95
        )
    
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
    
    def _analyze_source_dependencies(self, package_data: dict, workspace_path: str) -> list[str]:
        """Analyze source file dependencies based on package.json."""
        files = []
        
        # Check main entry point
        main_entry = package_data.get('main')
        if main_entry:
            main_path = os.path.join(workspace_path, main_entry)
            if os.path.isfile(main_path):
                files.append(main_path)
        
        # Check other entry points
        entry_points = [
            package_data.get('bin'),
            package_data.get('module'),
            package_data.get('exports'),
            package_data.get('types')
        ]
        
        for entry in entry_points:
            if entry:
                if isinstance(entry, dict):
                    # Handle exports object
                    for _key, value in entry.items():
                        if isinstance(value, str):
                            entry_path = os.path.join(workspace_path, value)
                            if os.path.isfile(entry_path):
                                files.append(entry_path)
                elif isinstance(entry, str):
                    entry_path = os.path.join(workspace_path, entry)
                    if os.path.isfile(entry_path):
                        files.append(entry_path)
        
        # Add common source directories if they exist
        source_dirs = ['src', 'lib', 'app', 'components', 'pages', 'api']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                # Add the directory itself to indicate it's needed
                files.append(source_path)
        
        return files
    
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
        globs = []
        
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
        
        # Add source file patterns based on subcommand
        if subcommand in ['build', 'run', 'test', 'lint', 'format']:
            # Common source directories
            source_dirs = ['src', 'lib', 'app', 'components', 'pages', 'api']
            for source_dir in source_dirs:
                source_path = os.path.join(ctx.workspace_path, source_dir)
                if os.path.isdir(source_path):
                    globs.append(os.path.join(source_path, "**/*"))
            
            # Add root JS/TS files
            globs.append(os.path.join(ctx.workspace_path, "*.js"))
            globs.append(os.path.join(ctx.workspace_path, "*.ts"))
            globs.append(os.path.join(ctx.workspace_path, "*.jsx"))
            globs.append(os.path.join(ctx.workspace_path, "*.tsx"))
        
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
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
