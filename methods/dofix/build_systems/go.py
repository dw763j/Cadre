"""
Go build system detector with intelligent dependency analysis.
"""

import re
import os
from pathlib import Path
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult
from .go_tooling import GoTooling, GoToolingUnavailable

class GoBuildSystemDetector(BuildSystemDetector):
    """Intelligent detector for Go build system commands."""
    
    @property
    def name(self) -> str:
        return "go"
    
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        if not command:
            return False
        
        go_pattern = r'^\s*go\s+(build|test|install|vet|mod|run|get|fmt|lint)\b'
        return bool(re.match(go_pattern, command, re.IGNORECASE))
    
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Intelligently analyze Go command and return files used from workspace."""
        command_parts = command.strip().split()
        if len(command_parts) < 2 or command_parts[0].lower() != 'go':
            raise ValueError(f"Invalid Go command: {command}")
        
        subcommand = command_parts[1].lower()
        
        # Try to use go tooling for precise file list first
        try:
            tooling = GoTooling(ctx.workspace_path)
            files = tooling.list_package_files()
            return BuildResult(
                files=files,
                globs=[],
                build_system=self.name,
                command_type=subcommand,
                confidence=0.98
            )
        except (ImportError, Exception) as e:
            logger.debug(f"Go tooling not available, falling back to intelligent analysis: {e}")
            # Fallback to intelligent analysis
            return self._intelligent_analysis(subcommand, ctx)
    
    def _intelligent_analysis(self, subcommand: str, ctx: BuildContext) -> BuildResult:
        """Intelligently analyze Go project structure and dependencies."""
        files = []
        globs = []
        
        # Always include go.mod and go.sum
        go_mod = os.path.join(ctx.workspace_path, "go.mod")
        go_sum = os.path.join(ctx.workspace_path, "go.sum")
        if os.path.isfile(go_mod):
            files.append(go_mod)
        if os.path.isfile(go_sum):
            files.append(go_sum)
        
        # Analyze go.mod for module information
        module_info = self._analyze_go_mod(go_mod)
        
        # Add source files based on subcommand and module structure
        if subcommand in ['build', 'install', 'test', 'vet', 'fmt', 'lint']:
            source_files = self._analyze_source_dependencies(ctx.workspace_path, module_info)
            files.extend(source_files)
            
            # Add test files for test commands
            if subcommand == 'test':
                test_files = self._find_test_files(ctx.workspace_path)
                files.extend(test_files)
        
        # Add vendor directory if it exists
        vendor_dir = os.path.join(ctx.workspace_path, "vendor")
        if os.path.isdir(vendor_dir):
            files.append(vendor_dir)
        
        # Add common Go configuration files
        config_files = self._find_go_configs(ctx.workspace_path)
        files.extend(config_files)
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.85
        )
    
    def _analyze_go_mod(self, go_mod_path: str) -> dict:
        """Analyze go.mod file for module information."""
        module_info = {
            'module_name': '',
            'go_version': '',
            'dependencies': [],
            'replace_directives': []
        }
        
        if not os.path.isfile(go_mod_path):
            return module_info
        
        try:
            with open(go_mod_path) as f:
                content = f.read()
            
            lines = content.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith('module '):
                    module_info['module_name'] = line.split(' ', 1)[1]
                elif line.startswith('go '):
                    module_info['go_version'] = line.split(' ', 1)[1]
                elif line.startswith('require '):
                    # Parse require directive
                    parts = line.split(' ')
                    if len(parts) >= 3:
                        module_info['dependencies'].append({
                            'module': parts[1],
                            'version': parts[2] if len(parts) > 2 else ''
                        })
                elif line.startswith('replace '):
                    # Parse replace directive
                    parts = line.split(' ')
                    if len(parts) >= 3:
                        module_info['replace_directives'].append({
                            'from': parts[1],
                            'to': parts[2]
                        })
                        
        except Exception as e:
            logger.debug(f"Failed to parse go.mod: {e}")
        
        return module_info
    
    def _analyze_source_dependencies(self, workspace_path: str, module_info: dict) -> list[str]:
        """Analyze source file dependencies based on Go module structure."""
        files = []
        
        # Look for main packages (entry points)
        main_packages = self._find_main_packages(workspace_path)
        files.extend(main_packages)
        
        # Look for common Go source directories
        source_dirs = ['cmd', 'internal', 'pkg', 'api', 'handlers', 'services']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                files.append(source_path)
        
        # Add root Go files
        root_go_files = [f for f in os.listdir(workspace_path) 
                         if f.endswith('.go') and os.path.isfile(os.path.join(workspace_path, f))]
        for go_file in root_go_files:
            files.append(os.path.join(workspace_path, go_file))
        
        return files
    
    def _find_main_packages(self, workspace_path: str) -> list[str]:
        """Find Go main packages (entry points)."""
        main_files = []
        
        # Common main package locations
        main_locations = ['cmd', 'cmd/main', 'main', 'app']
        
        for location in main_locations:
            location_path = os.path.join(workspace_path, location)
            if os.path.isdir(location_path):
                # Look for main.go files in this directory
                for root, _dirs, filenames in os.walk(location_path):
                    for filename in filenames:
                        if filename.endswith('.go'):
                            file_path = os.path.join(root, filename)
                            if self._contains_main_function(file_path):
                                main_files.append(file_path)
        
        # Also check root directory for main.go
        root_main = os.path.join(workspace_path, 'main.go')
        if os.path.isfile(root_main) and self._contains_main_function(root_main):
            main_files.append(root_main)
        
        return main_files
    
    def _contains_main_function(self, go_file_path: str) -> bool:
        """Check if a Go file contains a main function."""
        try:
            with open(go_file_path) as f:
                content = f.read()
            
            # Simple check for main function
            return 'func main()' in content or 'func main(' in content
        except Exception:
            return False
    
    def _find_test_files(self, workspace_path: str) -> list[str]:
        """Find Go test files and test directories."""
        test_items = []
        
        # Common test directories
        test_dirs = ['test', 'tests', 'testdata']
        for test_dir in test_dirs:
            test_path = os.path.join(workspace_path, test_dir)
            if os.path.isdir(test_path):
                test_items.append(test_path)
        
        # Look for *_test.go files
        for root, _dirs, filenames in os.walk(workspace_path):
            for filename in filenames:
                if filename.endswith('_test.go'):
                    test_items.append(os.path.join(root, filename))
        
        return test_items
    
    def _find_go_configs(self, workspace_path: str) -> list[str]:
        """Find Go-related configuration files."""
        config_files = [
            '.golangci.yml', '.golangci.yaml', '.golangci.toml',
            'go.work', 'go.work.sum', '.go-version'
        ]
        
        found_configs = []
        for config_file in config_files:
            config_path = os.path.join(workspace_path, config_file)
            if os.path.isfile(config_path):
                found_configs.append(config_path)
        
        return found_configs
    
    def _fallback_analysis(self, subcommand: str, ctx: BuildContext) -> BuildResult:
        """Fallback analysis when intelligent analysis is not available."""
        files = []
        globs = []
        
        # Always include go.mod and go.sum
        go_mod = os.path.join(ctx.workspace_path, "go.mod")
        go_sum = os.path.join(ctx.workspace_path, "go.sum")
        if os.path.isfile(go_mod):
            files.append(go_mod)
        if os.path.isfile(go_sum):
            files.append(go_sum)
        
        # Add source file patterns
        if subcommand in ['build', 'install', 'test', 'vet', 'fmt', 'lint']:
            globs.append(os.path.join(ctx.workspace_path, "**/*.go"))
        
        # Add vendor directory if it exists
        vendor_dir = os.path.join(ctx.workspace_path, "vendor")
        if os.path.isdir(vendor_dir):
            globs.append(os.path.join(vendor_dir, "**/*"))
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
