"""
TypeScript build system detector with intelligent dependency analysis.
"""

import re
import os
import json
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class TypeScriptBuildSystemDetector(BuildSystemDetector):
    """Intelligent detector for TypeScript build system commands."""
    
    @property
    def name(self) -> str:
        return "typescript"
    
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        if not command:
            return False
        
        # Match TypeScript build tool commands
        ts_patterns = [
            r'^\s*tsc\s+\w+',  # TypeScript compiler
            r'^\s*webpack\s+\w+',  # Webpack
            r'^\s*vite\s+\w+',  # Vite
            r'^\s*esbuild\s+\w+',  # esbuild
            r'^\s*rollup\s+\w+',  # Rollup
            r'^\s*parcel\s+\w+',  # Parcel
            r'^\s*ts-node\s+\w+',  # ts-node
            r'^\s*tsx\s+\w+'  # tsx
        ]
        
        for pattern in ts_patterns:
            if re.match(pattern, command, re.IGNORECASE):
                return True
        
        return False
    
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Intelligently analyze TypeScript command and return files used from workspace."""
        command_parts = command.strip().split()
        if len(command_parts) < 2:
            raise ValueError(f"Invalid TypeScript command: {command}")
        
        tool = command_parts[0].lower()
        subcommand = command_parts[1].lower()
        
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
        """Intelligently analyze TypeScript project structure and dependencies."""
        files = []
        globs = []
        
        # Find and parse TypeScript project configuration files
        config_files = self._find_typescript_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Analyze based on tool and subcommand
        if tool == 'tsc':
            # TypeScript compiler
            if subcommand in ['--build', '-b', '--watch', '-w']:
                # For build commands, include source files
                source_files = self._analyze_typescript_sources(ctx.workspace_path)
                files.extend(source_files)
                
        elif tool in ['webpack', 'vite', 'esbuild', 'rollup', 'parcel']:
            # Bundler tools
            if subcommand in ['build', 'dev', 'serve', 'start']:
                source_files = self._analyze_typescript_sources(ctx.workspace_path)
                files.extend(source_files)
                
                # Add bundler configuration files
                bundler_configs = self._find_bundler_configs(ctx.workspace_path, tool)
                files.extend(bundler_configs)
                
        elif tool in ['ts-node', 'tsx']:
            # TypeScript runtime tools
            source_files = self._analyze_typescript_sources(ctx.workspace_path)
            files.extend(source_files)
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.9
        )
    
    def _find_typescript_configs(self, workspace_path: str) -> list[str]:
        """Find TypeScript project configuration files."""
        config_files = []
        
        # TypeScript configuration files
        ts_configs = [
            'tsconfig.json', 'tsconfig.*.json', 'tsconfig.base.json',
            'tsconfig.app.json', 'tsconfig.lib.json', 'tsconfig.test.json'
        ]
        
        import glob
        for pattern in ts_configs:
            matches = glob.glob(os.path.join(workspace_path, pattern))
            config_files.extend(matches)
        
        # Package.json (for scripts and dependencies)
        package_json = os.path.join(workspace_path, 'package.json')
        if os.path.isfile(package_json):
            config_files.append(package_json)
            
            # Parse package.json for TypeScript-related information
            try:
                with open(package_json) as f:
                    package_data = json.load(f)
                
                # Look for TypeScript dependencies
                dependencies = package_data.get('dependencies', {})
                dev_dependencies = package_data.get('devDependencies', {})
                
                ts_deps = ['typescript', '@types/*', 'ts-node', 'tsx']
                for dep in ts_deps:
                    if any(dep in key for key in dependencies.keys()) or any(dep in key for key in dev_dependencies.keys()):
                        # Add package.json if TypeScript is used
                        if package_json not in config_files:
                            config_files.append(package_json)
                        break
                        
            except Exception as e:
                logger.debug(f"Failed to parse package.json: {e}")
        
        # TypeScript declaration files
        declaration_files = ['types.d.ts', 'global.d.ts', '*.d.ts']
        for pattern in declaration_files:
            if '*' in pattern:
                matches = glob.glob(os.path.join(workspace_path, pattern))
                config_files.extend(matches)
            else:
                decl_path = os.path.join(workspace_path, pattern)
                if os.path.isfile(decl_path):
                    config_files.append(decl_path)
        
        return config_files
    
    def _find_bundler_configs(self, workspace_path: str, tool: str) -> list[str]:
        """Find bundler configuration files."""
        bundler_configs = []
        
        # Webpack configuration
        if tool == 'webpack':
            webpack_configs = [
                'webpack.config.js', 'webpack.config.ts', 'webpack.config.mjs',
                'webpack.common.js', 'webpack.dev.js', 'webpack.prod.js'
            ]
            for config in webpack_configs:
                config_path = os.path.join(workspace_path, config)
                if os.path.isfile(config_path):
                    bundler_configs.append(config_path)
        
        # Vite configuration
        elif tool == 'vite':
            vite_configs = ['vite.config.js', 'vite.config.ts', 'vite.config.mjs']
            for config in vite_configs:
                config_path = os.path.join(workspace_path, config)
                if os.path.isfile(config_path):
                    bundler_configs.append(config_path)
        
        # esbuild configuration
        elif tool == 'esbuild':
            esbuild_configs = ['esbuild.config.js', 'esbuild.config.ts', 'esbuild.config.mjs']
            for config in esbuild_configs:
                config_path = os.path.join(workspace_path, config)
                if os.path.isfile(config_path):
                    bundler_configs.append(config_path)
        
        # Rollup configuration
        elif tool == 'rollup':
            rollup_configs = ['rollup.config.js', 'rollup.config.ts', 'rollup.config.mjs']
            for config in rollup_configs:
                config_path = os.path.join(workspace_path, config)
                if os.path.isfile(config_path):
                    bundler_configs.append(config_path)
        
        # Parcel configuration
        elif tool == 'parcel':
            parcel_configs = ['.parcelrc', 'parcel.config.js', 'parcel.config.ts']
            for config in parcel_configs:
                config_path = os.path.join(workspace_path, config)
                if os.path.isfile(config_path):
                    bundler_configs.append(config_path)
        
        return bundler_configs
    
    def _analyze_typescript_sources(self, workspace_path: str) -> list[str]:
        """Analyze TypeScript source file dependencies."""
        files = []
        
        # Common TypeScript source directories
        source_dirs = ['src', 'app', 'main', 'core', 'components', 'pages', 'api', 'lib']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                files.append(source_path)
                # Also add TypeScript files within these directories
                try:
                    for root, _dirs, filenames in os.walk(source_path):
                        for filename in filenames:
                            if filename.endswith(('.ts', '.tsx')):
                                file_path = os.path.join(root, filename)
                                if os.path.isfile(file_path):
                                    files.append(file_path)
                except (OSError, PermissionError):
                    pass
        
        # Add root TypeScript files
        try:
            root_ts_files = [f for f in os.listdir(workspace_path) 
                            if f.endswith(('.ts', '.tsx')) and os.path.isfile(os.path.join(workspace_path, f))]
            for ts_file in root_ts_files:
                files.append(os.path.join(workspace_path, ts_file))
        except (OSError, PermissionError):
            pass
        
        # Look for common entry points
        common_entry_points = ['main.ts', 'index.ts', 'app.ts', 'server.ts', 'main.tsx', 'index.tsx', 'App.tsx']
        for entry_point in common_entry_points:
            entry_path = os.path.join(workspace_path, entry_point)
            if os.path.isfile(entry_path):
                files.append(entry_path)
        
        # Look for TypeScript declaration files
        import glob
        declaration_patterns = ['*.d.ts', 'types/*.d.ts', 'typings/*.d.ts']
        for pattern in declaration_patterns:
            matches = glob.glob(os.path.join(workspace_path, pattern))
            files.extend(matches)
        
        # Parse tsconfig.json for additional source files
        tsconfig_path = os.path.join(workspace_path, 'tsconfig.json')
        if os.path.isfile(tsconfig_path):
            try:
                with open(tsconfig_path) as f:
                    tsconfig_data = json.load(f)
                
                # Add include patterns
                include_patterns = tsconfig_data.get('include', [])
                for pattern in include_patterns:
                    if '*' in pattern:
                        matches = glob.glob(os.path.join(workspace_path, pattern))
                        files.extend(matches)
                    else:
                        include_path = os.path.join(workspace_path, pattern)
                        if os.path.isfile(include_path):
                            files.append(include_path)
                        elif os.path.isdir(include_path):
                            files.append(include_path)
                
                # Add files array
                files_array = tsconfig_data.get('files', [])
                for file_path in files_array:
                    full_path = os.path.join(workspace_path, file_path)
                    if os.path.isfile(full_path):
                        files.append(full_path)
                        
            except Exception as e:
                logger.debug(f"Failed to parse tsconfig.json: {e}")
        
        return files
    
    def _basic_analysis(self, ctx: BuildContext, subcommand: str) -> BuildResult:
        """Fallback to basic analysis when intelligent analysis fails."""
        files = []
        globs = []
        
        # Add common TypeScript files
        config_files = self._find_typescript_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Add source file patterns
        if subcommand in ['build', 'test', 'run', 'compile']:
            globs.append(os.path.join(ctx.workspace_path, "**/*.ts"))
            globs.append(os.path.join(ctx.workspace_path, "**/*.tsx"))
            globs.append(os.path.join(ctx.workspace_path, "**/*.d.ts"))
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
