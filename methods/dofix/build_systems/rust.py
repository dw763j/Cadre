"""
Rust build system detector with intelligent dependency analysis.
"""

import re
import os
import toml
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class RustBuildSystemDetector(BuildSystemDetector):
    """Intelligent detector for Rust build system commands."""
    
    @property
    def name(self) -> str:
        return "rust"
    
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        if not command:
            return False
        
        # Match Rust cargo commands
        rust_patterns = [
            r'^\s*cargo\s+(build|test|run|check|clippy|fmt|publish|install)\b',
            r'^\s*rustc\s+\w+',  # Rust compiler
            r'^\s*rustup\s+\w+'  # Rust toolchain manager
        ]
        
        for pattern in rust_patterns:
            if re.match(pattern, command, re.IGNORECASE):
                return True
        
        return False
    
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Intelligently analyze Rust command and return files used from workspace."""
        command_parts = command.strip().split()
        if len(command_parts) < 2 or command_parts[0].lower() != 'cargo':
            raise ValueError(f"Invalid Rust command: {command}")
        
        subcommand = command_parts[1].lower()
        
        # Try intelligent analysis first
        try:
            result = self._intelligent_analysis(ctx, subcommand)
            if result:
                return result
        except Exception as e:
            logger.debug(f"Intelligent analysis failed, falling back to basic: {e}")
        
        # Fallback to basic analysis
        return self._basic_analysis(ctx, subcommand)
    
    def _intelligent_analysis(self, ctx: BuildContext, subcommand: str) -> BuildResult | None:
        """Intelligently analyze Rust project structure and dependencies."""
        files = []
        globs = []
        
        # Find and parse Cargo.toml
        cargo_toml = os.path.join(ctx.workspace_path, 'Cargo.toml')
        if not os.path.isfile(cargo_toml):
            return None
        
        files.append(cargo_toml)
        
        # Parse Cargo.toml for project information
        project_info = self._parse_cargo_toml(cargo_toml)
        
        # Add Cargo.lock if it exists
        cargo_lock = os.path.join(ctx.workspace_path, 'Cargo.lock')
        if os.path.isfile(cargo_lock):
            files.append(cargo_lock)
        
        # Analyze based on subcommand
        if subcommand in ['build', 'run', 'check', 'clippy']:
            # For build/run commands, include source files
            source_files = self._analyze_rust_sources(ctx.workspace_path, project_info)
            files.extend(source_files)
            
        elif subcommand == 'test':
            # For test commands, include test files
            test_files = self._find_rust_test_files(ctx.workspace_path)
            files.extend(test_files)
            
            # Also include source files for testing
            source_files = self._analyze_rust_sources(ctx.workspace_path, project_info)
            files.extend(source_files)
            
        elif subcommand == 'publish':
            # For publish, include all source files and documentation
            source_files = self._analyze_rust_sources(ctx.workspace_path, project_info)
            files.extend(source_files)
            
            # Add documentation files
            doc_files = self._find_rust_doc_files(ctx.workspace_path)
            files.extend(doc_files)
        
        # Add Rust configuration files
        config_files = self._find_rust_configs(ctx.workspace_path)
        files.extend(config_files)
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.95
        )
    
    def _parse_cargo_toml(self, cargo_toml_path: str) -> dict:
        """Parse Cargo.toml file for project information."""
        project_info = {
            'package': {},
            'dependencies': {},
            'dev_dependencies': {},
            'build_dependencies': {},
            'workspace': {},
            'targets': []
        }
        
        try:
            with open(cargo_toml_path) as f:
                content = f.read()
            
            # Parse TOML content
            cargo_data = toml.loads(content)
            
            # Extract package information
            if 'package' in cargo_data:
                project_info['package'] = cargo_data['package']
            
            # Extract dependencies
            if 'dependencies' in cargo_data:
                project_info['dependencies'] = cargo_data['dependencies']
            
            if 'dev-dependencies' in cargo_data:
                project_info['dev_dependencies'] = cargo_data['dev-dependencies']
            
            if 'build-dependencies' in cargo_data:
                project_info['build_dependencies'] = cargo_data['build-dependencies']
            
            # Extract workspace information
            if 'workspace' in cargo_data:
                project_info['workspace'] = cargo_data['workspace']
            
            # Extract targets
            if 'lib' in cargo_data:
                project_info['targets'].append('lib')
            
            if 'bin' in cargo_data:
                project_info['targets'].append('bin')
            
            # Look for [[bin]] sections
            if 'bin' in cargo_data and isinstance(cargo_data['bin'], list):
                for bin_target in cargo_data['bin']:
                    if isinstance(bin_target, dict) and 'name' in bin_target:
                        project_info['targets'].append(f"bin:{bin_target['name']}")
            
            # Look for [[example]] sections
            if 'example' in cargo_data and isinstance(cargo_data['example'], list):
                for example_target in cargo_data['example']:
                    if isinstance(example_target, dict) and 'name' in example_target:
                        project_info['targets'].append(f"example:{example_target['name']}")
            
            # Look for [[test]] sections
            if 'test' in cargo_data and isinstance(cargo_data['test'], list):
                for test_target in cargo_data['test']:
                    if isinstance(test_target, dict) and 'name' in test_target:
                        project_info['targets'].append(f"test:{test_target['name']}")
            
            # Look for [[bench]] sections
            if 'bench' in cargo_data and isinstance(cargo_data['bench'], list):
                for bench_target in cargo_data['bench']:
                    if isinstance(bench_target, dict) and 'name' in bench_target:
                        project_info['targets'].append(f"bench:{bench_target['name']}")
                        
        except Exception as e:
            logger.debug(f"Failed to parse Cargo.toml: {e}")
        
        return project_info
    
    def _analyze_rust_sources(self, workspace_path: str, project_info: dict) -> list[str]:
        """Analyze Rust source file dependencies."""
        files = []
        
        # Common Rust source directories
        source_dirs = ['src', 'examples', 'tests', 'benches']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                files.append(source_path)
                # Also add Rust files within these directories
                try:
                    for root, _dirs, filenames in os.walk(source_path):
                        for filename in filenames:
                            if filename.endswith('.rs'):
                                file_path = os.path.join(root, filename)
                                if os.path.isfile(file_path):
                                    files.append(file_path)
                except (OSError, PermissionError):
                    pass
        
        # Add root Rust files
        try:
            root_rust_files = [f for f in os.listdir(workspace_path) 
                               if f.endswith('.rs') and os.path.isfile(os.path.join(workspace_path, f))]
            for rust_file in root_rust_files:
                files.append(os.path.join(workspace_path, rust_file))
        except (OSError, PermissionError):
            pass  # Skip if we can't list directory
        
        # Look for main.rs and lib.rs
        main_rs = os.path.join(workspace_path, 'src', 'main.rs')
        lib_rs = os.path.join(workspace_path, 'src', 'lib.rs')
        
        if os.path.isfile(main_rs):
            files.append(main_rs)
        if os.path.isfile(lib_rs):
            files.append(lib_rs)
        
        # Add build.rs if it exists
        build_rs = os.path.join(workspace_path, 'build.rs')
        if os.path.isfile(build_rs):
            files.append(build_rs)
        
        return files
    
    def _find_rust_test_files(self, workspace_path: str) -> list[str]:
        """Find Rust test files and directories."""
        test_items = []
        
        # Common test directories
        test_dirs = ['tests', 'benches']
        for test_dir in test_dirs:
            test_path = os.path.join(workspace_path, test_dir)
            if os.path.isdir(test_path):
                test_items.append(test_path)
        
        # Look for test files in src directory
        src_test_path = os.path.join(workspace_path, 'src')
        if os.path.isdir(src_test_path):
            for root, _dirs, filenames in os.walk(src_test_path):
                for filename in filenames:
                    if filename.endswith('.rs'):
                        file_path = os.path.join(root, filename)
                        # Check if file contains tests
                        if self._contains_rust_tests(file_path):
                            test_items.append(file_path)
        
        return test_items
    
    def _contains_rust_tests(self, rust_file_path: str) -> bool:
        """Check if a Rust file contains tests."""
        try:
            with open(rust_file_path) as f:
                content = f.read()
            
            # Look for test modules and functions
            test_indicators = [
                '#[cfg(test)]', '#[test]', '#[tokio::test]',
                'mod tests', 'fn test_', 'fn it_'
            ]
            
            return any(indicator in content for indicator in test_indicators)
        except Exception:
            return False
    
    def _find_rust_doc_files(self, workspace_path: str) -> list[str]:
        """Find Rust documentation files."""
        doc_items = []
        
        # Common documentation directories
        doc_dirs = ['docs', 'doc', 'documentation']
        for doc_dir in doc_dirs:
            doc_path = os.path.join(workspace_path, doc_dir)
            if os.path.isdir(doc_path):
                doc_items.append(doc_path)
        
        # Look for README files
        readme_files = ['README.md', 'README.txt', 'README.rst']
        for readme in readme_files:
            readme_path = os.path.join(workspace_path, readme)
            if os.path.isfile(readme_path):
                doc_items.append(readme_path)
        
        # Look for documentation in src
        src_doc_path = os.path.join(workspace_path, 'src')
        if os.path.isdir(src_doc_path):
            for root, _dirs, filenames in os.walk(src_doc_path):
                for filename in filenames:
                    if filename.endswith('.md') and 'readme' in filename.lower():
                        doc_items.append(os.path.join(root, filename))
        
        return doc_items
    
    def _find_rust_configs(self, workspace_path: str) -> list[str]:
        """Find Rust-related configuration files."""
        config_files = []
        
        # Rust toolchain configuration
        rust_configs = [
            'rust-toolchain.toml', 'rust-toolchain',
            '.rustfmt.toml', '.cargo/config.toml', '.cargo/config'
        ]
        
        for config in rust_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # IDE configuration
        ide_configs = [
            '.vscode/settings.json', '.idea/workspace.xml',
            '.editorconfig'
        ]
        
        for config in ide_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        return config_files
    
    def _basic_analysis(self, ctx: BuildContext, subcommand: str) -> BuildResult:
        """Fallback to basic analysis when intelligent analysis fails."""
        files = []
        globs = []
        
        # Add common Rust files
        cargo_toml = os.path.join(ctx.workspace_path, 'Cargo.toml')
        if os.path.isfile(cargo_toml):
            files.append(cargo_toml)
        
        cargo_lock = os.path.join(ctx.workspace_path, 'Cargo.lock')
        if os.path.isfile(cargo_lock):
            files.append(cargo_lock)
        
        # Add source file patterns
        if subcommand in ['build', 'test', 'run', 'check', 'clippy']:
            globs.append(os.path.join(ctx.workspace_path, "**/*.rs"))
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
