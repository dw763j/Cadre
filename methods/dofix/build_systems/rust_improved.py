"""
Improved Rust build system detector with source-level dependency analysis.

This implementation focuses on analyzing actual source code dependencies
without requiring external packages, making it suitable for environments
where only standard language runtimes are available.
"""

import re
import os
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class RustDependencyAnalyzer:
    """Analyzes Rust source code dependencies without external packages."""
    
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
        """Extract dependencies from a single Rust file."""
        if not os.path.isfile(file_path) or not file_path.endswith('.rs'):
            return set()
        
        dependencies = set()
        
        try:
            with open(file_path, encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.debug(f"Failed to read file {file_path}: {e}")
            return dependencies
        
        # Extract module declarations and imports
        dependencies.update(self._extract_rust_imports(content, file_path))
        dependencies.update(self._extract_rust_modules(content, file_path))
        dependencies.update(self._extract_rust_macros(content, file_path))
        
        return dependencies
    
    def _extract_rust_imports(self, content: str, file_path: str) -> set[str]:
        """Extract Rust import statements."""
        dependencies = set()
        
        # Standard library imports (usually don't need to track)
        # but we can track if they reference local modules
        
        # External crate imports
        extern_crate_pattern = r'extern\s+crate\s+(\w+)'
        for match in re.finditer(extern_crate_pattern, content):
            crate_name = match.group(1)
            # Look for local crate files
            crate_file = self._find_local_crate_file(crate_name)
            if crate_file:
                dependencies.add(crate_file)
        
        # Use statements that might reference local modules
        use_patterns = [
            r'use\s+(\w+(?:::\w+)*)',
            r'use\s+(\w+(?:::\w+)*)\s*;',
            r'use\s+(\w+(?:::\w+)*)\s*as\s+\w+'
        ]
        
        for pattern in use_patterns:
            for match in re.finditer(pattern, content):
                module_path = match.group(1)
                resolved_path = self._resolve_rust_module_path(module_path, file_path)
                if resolved_path:
                    dependencies.add(resolved_path)
        
        return dependencies
    
    def _extract_rust_modules(self, content: str, file_path: str) -> set[str]:
        """Extract Rust module declarations."""
        dependencies = set()
        
        # Module declarations
        mod_patterns = [
            r'mod\s+(\w+)\s*;',  # mod module_name;
            r'mod\s+(\w+)\s*{',  # mod module_name { ... }
        ]
        
        for pattern in mod_patterns:
            for match in re.finditer(pattern, content):
                module_name = match.group(1)
                module_file = self._find_module_file(module_name, file_path)
                if module_file:
                    dependencies.add(module_file)
        
        return dependencies
    
    def _extract_rust_macros(self, content: str, file_path: str) -> set[str]:
        """Extract Rust macro usage."""
        dependencies = set()
        
        # Macro invocations
        macro_patterns = [
            r'(\w+)!\s*[({]',  # macro_name! { ... } or macro_name!( ... )
            r'(\w+)!\s*\[',    # macro_name![ ... ]
        ]
        
        for pattern in macro_patterns:
            for match in re.finditer(pattern, content):
                macro_name = match.group(1)
                # Look for local macro definitions
                macro_file = self._find_macro_file(macro_name, file_path)
                if macro_file:
                    dependencies.add(macro_file)
        
        return dependencies
    
    def _find_local_crate_file(self, crate_name: str) -> str | None:
        """Find local crate file for a given crate name."""
        # Look for lib.rs or main.rs files that might define this crate
        possible_files = [
            os.path.join(self.workspace_path, 'src', 'lib.rs'),
            os.path.join(self.workspace_path, 'src', 'main.rs'),
            os.path.join(self.workspace_path, 'lib.rs'),
            os.path.join(self.workspace_path, 'main.rs')
        ]
        
        for file_path in possible_files:
            if os.path.isfile(file_path):
                try:
                    with open(file_path, encoding='utf-8') as f:
                        content = f.read()
                    # Check if this file defines the crate
                    if f'crate_name = "{crate_name}"' in content or f'name = "{crate_name}"' in content:
                        return file_path
                except Exception:
                    continue
        
        return None
    
    def _resolve_rust_module_path(self, module_path: str, from_file: str) -> str | None:
        """Resolve a Rust module path to a file path."""
        if not module_path:
            return None
        
        # Handle relative module paths
        if module_path.startswith('crate::'):
            # Absolute path from crate root
            module_path = module_path[7:]  # Remove 'crate::'
            base_path = self.workspace_path
        elif module_path.startswith('super::'):
            # Relative path to parent
            from_dir = os.path.dirname(from_file)
            parent_dir = os.path.dirname(from_dir)
            module_path = module_path[8:]  # Remove 'super::'
            base_path = parent_dir
        elif module_path.startswith('self::'):
            # Relative path from current module
            from_dir = os.path.dirname(from_file)
            module_path = module_path[6:]  # Remove 'self::'
            base_path = from_dir
        else:
            # Relative path from current file
            from_dir = os.path.dirname(from_file)
            base_path = from_dir
        
        # Convert module path to file path
        if module_path:
            parts = module_path.split('::')
            file_path = os.path.join(base_path, *parts)
            
            # Try different file extensions
            extensions = ['.rs', '']
            for ext in extensions:
                test_path = file_path + ext
                if os.path.isfile(test_path):
                    return test_path
            
            # Check if it's a directory with mod.rs
            if os.path.isdir(file_path):
                mod_rs = os.path.join(file_path, 'mod.rs')
                if os.path.isfile(mod_rs):
                    return mod_rs
        
        return None
    
    def _find_module_file(self, module_name: str, from_file: str) -> str | None:
        """Find a module file for a given module name."""
        from_dir = os.path.dirname(from_file)
        
        # Look for module_name.rs
        module_file = os.path.join(from_dir, f'{module_name}.rs')
        if os.path.isfile(module_file):
            return module_file
        
        # Look for module_name/mod.rs
        module_dir = os.path.join(from_dir, module_name)
        if os.path.isdir(module_dir):
            mod_rs = os.path.join(module_dir, 'mod.rs')
            if os.path.isfile(mod_rs):
                return mod_rs
        
        return None
    
    def _find_macro_file(self, macro_name: str, from_file: str) -> str | None:
        """Find a macro definition file."""
        # Look for macro definitions in the same directory and parent directories
        current_dir = os.path.dirname(from_file)
        
        while current_dir.startswith(self.workspace_path):
            # Look for macro definitions in this directory
            for filename in os.listdir(current_dir):
                if filename.endswith('.rs'):
                    file_path = os.path.join(current_dir, filename)
                    try:
                        with open(file_path, encoding='utf-8') as f:
                            content = f.read()
                        # Check for macro definition
                        if f'macro_rules! {macro_name}' in content or f'macro_rules! {macro_name}!' in content:
                            return file_path
                    except Exception:
                        continue
            
            # Move to parent directory
            parent_dir = os.path.dirname(current_dir)
            if parent_dir == current_dir:
                break
            current_dir = parent_dir
        
        return None


class ImprovedRustBuildSystemDetector(BuildSystemDetector):
    """Improved Rust build system detector with source-level dependency analysis."""
    
    @property
    def name(self) -> str:
        return "rust_improved"
    
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
            
            # Analyze source dependencies using dependency analyzer
            entry_files = self._get_entry_files(project_info, ctx.workspace_path)
            if entry_files:
                analyzer = RustDependencyAnalyzer(ctx.workspace_path)
                source_dependencies = analyzer.analyze_dependencies(entry_files)
                files.extend(list(source_dependencies))
            
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
            globs=[],
            build_system=self.name,
            command_type=subcommand,
            confidence=0.95
        )
    
    def _parse_cargo_toml(self, cargo_toml_path: str) -> dict:
        """Parse Cargo.toml without external dependencies."""
        project_info = {
            'package': {},
            'dependencies': {},
            'dev_dependencies': {},
            'build_dependencies': {},
            'workspace': {},
            'targets': []
        }
        
        try:
            with open(cargo_toml_path, encoding='utf-8') as f:
                content = f.read()
            
            # Simple TOML parsing for common fields
            lines = content.split('\n')
            current_section = None
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Check for section headers
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1]
                    continue
                
                # Parse key-value pairs
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    
                    if current_section == 'package':
                        if key in ['name', 'version', 'edition']:
                            project_info['package'][key] = value
                    elif current_section == 'dependencies':
                        project_info['dependencies'][key] = value
                    elif current_section == 'dev-dependencies':
                        project_info['dev_dependencies'][key] = value
                    elif current_section == 'build-dependencies':
                        project_info['build_dependencies'][key] = value
                    
                    # Check for target definitions
                    if key == 'name' and current_section and current_section.startswith('[[bin]]'):
                        project_info['targets'].append(f"bin:{value}")
                    elif key == 'name' and current_section and current_section.startswith('[[lib]]'):
                        project_info['targets'].append(f"lib:{value}")
                    elif key == 'name' and current_section and current_section.startswith('[[example]]'):
                        project_info['targets'].append(f"example:{value}")
                    elif key == 'name' and current_section and current_section.startswith('[[test]]'):
                        project_info['targets'].append(f"test:{value}")
                    elif key == 'name' and current_section and current_section.startswith('[[bench]]'):
                        project_info['targets'].append(f"bench:{value}")
                        
        except Exception as e:
            logger.debug(f"Failed to parse Cargo.toml: {e}")
        
        return project_info
    
    def _get_entry_files(self, project_info: dict, workspace_path: str) -> list[str]:
        """Get entry files from project information."""
        entry_files = []
        
        # Look for main.rs and lib.rs
        main_rs = os.path.join(workspace_path, 'src', 'main.rs')
        lib_rs = os.path.join(workspace_path, 'src', 'lib.rs')
        
        if os.path.isfile(main_rs):
            entry_files.append(main_rs)
        if os.path.isfile(lib_rs):
            entry_files.append(lib_rs)
        
        # Look for binary targets
        for target in project_info['targets']:
            if target.startswith('bin:'):
                bin_name = target[4:]
                bin_path = os.path.join(workspace_path, 'src', 'bin', f'{bin_name}.rs')
                if os.path.isfile(bin_path):
                    entry_files.append(bin_path)
        
        # If no entry points found, look for common entry files
        if not entry_files:
            common_entries = ['src/main.rs', 'src/lib.rs', 'main.rs', 'lib.rs']
            for entry in common_entries:
                entry_path = os.path.join(workspace_path, entry)
                if os.path.isfile(entry_path):
                    entry_files.append(entry_path)
                    break
        
        return entry_files
    
    def _analyze_rust_sources(self, workspace_path: str, project_info: dict) -> list[str]:
        """Analyze Rust source file dependencies."""
        files = []
        
        # Common Rust source directories
        source_dirs = ['src', 'examples', 'tests', 'benches']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                files.append(source_path)
        
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
            with open(rust_file_path, encoding='utf-8') as f:
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
        
        # Add common Rust files
        cargo_toml = os.path.join(ctx.workspace_path, 'Cargo.toml')
        if os.path.isfile(cargo_toml):
            files.append(cargo_toml)
        
        cargo_lock = os.path.join(ctx.workspace_path, 'Cargo.lock')
        if os.path.isfile(cargo_lock):
            files.append(cargo_lock)
        
        # Add source file patterns
        if subcommand in ['build', 'test', 'run', 'check', 'clippy']:
            # Add key source files instead of broad patterns
            key_files = [
                'src/main.rs', 'src/lib.rs', 'build.rs',
                'Cargo.toml', 'Cargo.lock'
            ]
            for key_file in key_files:
                file_path = os.path.join(ctx.workspace_path, key_file)
                if os.path.isfile(file_path):
                    files.append(file_path)
        
        return BuildResult(
            files=files,
            globs=[],
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
