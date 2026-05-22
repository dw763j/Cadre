"""
Improved PHP build system detector with source-level dependency analysis.

This implementation focuses on analyzing actual source code dependencies
without requiring external packages, making it suitable for environments
where only standard language runtimes are available.
"""

import re
import os
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class PHPDependencyAnalyzer:
    """Analyzes PHP source code dependencies without external packages."""
    
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
        """Extract dependencies from a single PHP file."""
        if not os.path.isfile(file_path) or not file_path.endswith('.php'):
            return set()
        
        dependencies = set()
        
        try:
            with open(file_path, encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.debug(f"Failed to read file {file_path}: {e}")
            return dependencies
        
        # Extract different types of dependencies
        dependencies.update(self._extract_php_includes(content, file_path))
        dependencies.update(self._extract_php_requires(content, file_path))
        dependencies.update(self._extract_php_autoload(content, file_path))
        dependencies.update(self._extract_php_namespaces(content, file_path))
        dependencies.update(self._extract_php_use_statements(content, file_path))
        
        return dependencies
    
    def _extract_php_includes(self, content: str, file_path: str) -> set[str]:
        """Extract PHP include/require statements."""
        dependencies = set()
        
        # Include and require statements
        include_patterns = [
            r'include\s+[\'"]([^\'"]+)[\'"]',
            r'require\s+[\'"]([^\'"]+)[\'"]',
            r'include_once\s+[\'"]([^\'"]+)[\'"]',
            r'require_once\s+[\'"]([^\'"]+)[\'"]'
        ]
        
        for pattern in include_patterns:
            for match in re.finditer(pattern, content):
                file_path_ref = match.group(1)
                resolved_path = self._resolve_php_path(file_path_ref, file_path)
                if resolved_path:
                    dependencies.add(resolved_path)
        
        return dependencies
    
    def _extract_php_requires(self, content: str, file_path: str) -> set[str]:
        """Extract PHP require statements."""
        dependencies = set()
        
        # Require statements (already covered in includes, but more specific)
        require_patterns = [
            r'require\s+[\'"]([^\'"]+)[\'"]',
            r'require_once\s+[\'"]([^\'"]+)[\'"]'
        ]
        
        for pattern in require_patterns:
            for match in re.finditer(pattern, content):
                file_path_ref = match.group(1)
                resolved_path = self._resolve_php_path(file_path_ref, file_path)
                if resolved_path:
                    dependencies.add(resolved_path)
        
        return dependencies
    
    def _extract_php_autoload(self, content: str, file_path: str) -> set[str]:
        """Extract PHP autoloader registrations."""
        dependencies = set()
        
        # Autoloader registrations
        autoload_patterns = [
            r'spl_autoload_register\s*\(\s*[\'"]([^\'"]+)[\'"]',
            r'spl_autoload_register\s*\(\s*function\s*\(\s*\$class\s*\)\s*{\s*require\s+[\'"]([^\'"]+)[\'"]',
            r'autoload\s*\(\s*[\'"]([^\'"]+)[\'"]'
        ]
        
        for pattern in autoload_patterns:
            for match in re.finditer(pattern, content):
                file_path_ref = match.group(1)
                resolved_path = self._resolve_php_path(file_path_ref, file_path)
                if resolved_path:
                    dependencies.add(resolved_path)
        
        return dependencies
    
    def _extract_php_namespaces(self, content: str, file_path: str) -> set[str]:
        """Extract PHP namespace declarations and look for related files."""
        dependencies = set()
        
        # Namespace declarations
        namespace_pattern = r'namespace\s+([^;]+);'
        for match in re.finditer(namespace_pattern, content):
            namespace = match.group(1).strip()
            # Look for files in the same namespace
            namespace_files = self._find_namespace_files(namespace, file_path)
            dependencies.update(namespace_files)
        
        return dependencies
    
    def _extract_php_use_statements(self, content: str, file_path: str) -> set[str]:
        """Extract PHP use statements."""
        dependencies = set()
        
        # Use statements
        use_patterns = [
            r'use\s+([^;]+);',
            r'use\s+([^;]+)\s+as\s+\w+;'
        ]
        
        for pattern in use_patterns:
            for match in re.finditer(pattern, content):
                use_statement = match.group(1).strip()
                # Look for files that might be referenced
                use_files = self._find_use_statement_files(use_statement, file_path)
                dependencies.update(use_files)
        
        return dependencies
    
    def _resolve_php_path(self, file_path_ref: str, from_file: str) -> str | None:
        """Resolve a PHP file path reference to an absolute file path."""
        if not file_path_ref or file_path_ref.startswith('http'):
            return None
        
        # Handle relative paths
        if file_path_ref.startswith('./'):
            from_dir = os.path.dirname(from_file)
            resolved_path = os.path.normpath(os.path.join(from_dir, file_path_ref))
        elif file_path_ref.startswith('../'):
            from_dir = os.path.dirname(from_file)
            resolved_path = os.path.normpath(os.path.join(from_dir, file_path_ref))
        elif file_path_ref.startswith('/'):
            # Absolute path from workspace root
            resolved_path = os.path.normpath(os.path.join(self.workspace_path, file_path_ref.lstrip('/')))
        else:
            # Relative path from current file
            from_dir = os.path.dirname(from_file)
            resolved_path = os.path.normpath(os.path.join(from_dir, file_path_ref))
        
        # Try different file extensions
        extensions = ['.php', '']
        for ext in extensions:
            test_path = resolved_path + ext
            if os.path.isfile(test_path):
                return test_path
        
        # Check if it's a directory with index.php
        if os.path.isdir(resolved_path):
            index_php = os.path.join(resolved_path, 'index.php')
            if os.path.isfile(index_php):
                return index_php
        
        return None
    
    def _find_namespace_files(self, namespace: str, from_file: str) -> set[str]:
        """Find files in the same namespace."""
        namespace_files = set()
        
        # Convert namespace to directory path
        namespace_parts = namespace.split('\\')
        namespace_dir = os.path.join(self.workspace_path, *namespace_parts)
        
        if os.path.isdir(namespace_dir):
            # Look for PHP files in this namespace directory
            try:
                for root, _dirs, filenames in os.walk(namespace_dir):
                    for filename in filenames:
                        if filename.endswith('.php'):
                            file_path = os.path.join(root, filename)
                            if os.path.isfile(file_path):
                                namespace_files.add(file_path)
            except (OSError, PermissionError):
                pass
        
        return namespace_files
    
    def _find_use_statement_files(self, use_statement: str, from_file: str) -> set[str]:
        """Find files referenced in use statements."""
        use_files = set()
        
        # Convert use statement to potential file path
        use_parts = use_statement.split('\\')
        if len(use_parts) > 1:
            # Look for the class file
            class_name = use_parts[-1]
            namespace_path = '\\'.join(use_parts[:-1])
            
            # Try to find the file in the namespace
            namespace_dir = os.path.join(self.workspace_path, *namespace_path.split('\\'))
            potential_files = [
                os.path.join(namespace_dir, f'{class_name}.php'),
                os.path.join(namespace_dir, class_name, f'{class_name}.php')
            ]
            
            for potential_file in potential_files:
                if os.path.isfile(potential_file):
                    use_files.add(potential_file)
                    break
        
        return use_files


class ImprovedPHPBuildSystemDetector(BuildSystemDetector):
    """Improved PHP build system detector with source-level dependency analysis."""
    
    @property
    def name(self) -> str:
        return "php_improved"
    
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        if not command:
            return False
        
        # Match PHP build tool commands
        php_patterns = [
            r'^\s*composer\s+\w+',  # Composer
            r'^\s*php\s+\w+',  # PHP runtime
            r'^\s*phpunit\s+\w+',  # PHPUnit testing
            r'^\s*phpcs\s+\w+',  # PHP CodeSniffer
            r'^\s*phpcbf\s+\w+',  # PHP Code Beautifier
            r'^\s*phpmd\s+\w+',  # PHP Mess Detector
            r'^\s*phpstan\s+\w+',  # PHPStan static analysis
            r'^\s*psalm\s+\w+'  # Psalm static analysis
        ]
        
        for pattern in php_patterns:
            if re.match(pattern, command, re.IGNORECASE):
                return True
        
        return False
    
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Intelligently analyze PHP command and return files used from workspace."""
        command_parts = command.strip().split()
        if len(command_parts) < 2:
            raise ValueError(f"Invalid PHP command: {command}")
        
        tool = command_parts[0].lower()
        subcommand = command_parts[1].lower()
        
        # Try intelligent analysis first
        try:
            result = self._intelligent_analysis(ctx, tool, subcommand, command_parts)
            if result:
                return result
        except Exception as e:
            logger.debug(f"Intelligent analysis failed, falling back to basic: {e}")
        
        # Fallback to basic analysis
        return self._basic_analysis(ctx, subcommand)
    
    def _intelligent_analysis(self, ctx: BuildContext, tool: str, subcommand: str, command_parts: list[str]) -> BuildResult | None:
        """Intelligently analyze PHP project structure and dependencies."""
        files = []
        
        # Find and parse PHP project configuration files
        config_files = self._find_php_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Analyze based on tool and subcommand
        if tool == 'composer':
            if subcommand in ['install', 'update', 'require', 'remove']:
                # For dependency management, focus on composer files
                composer_files = self._find_composer_files(ctx.workspace_path)
                files.extend(composer_files)
                
            elif subcommand in ['dump-autoload', 'autoload']:
                # For autoload, include source files
                source_files = self._analyze_php_sources(ctx.workspace_path)
                files.extend(source_files)
                
            elif subcommand in ['test', 'run-scripts']:
                # For testing and scripts, include source and test files
                source_files = self._analyze_php_sources(ctx.workspace_path)
                files.extend(source_files)
                
                test_files = self._find_php_test_files(ctx.workspace_path)
                files.extend(test_files)
                
        elif tool in ['phpunit', 'phpcs', 'phpcbf', 'phpmd', 'phpstan', 'psalm']:
            # Testing and analysis tools
            test_files = self._find_php_test_files(ctx.workspace_path)
            files.extend(test_files)
            
            # Include source files for analysis
            source_files = self._analyze_php_sources(ctx.workspace_path)
            files.extend(source_files)
            
        elif tool == 'php':
            # PHP runtime commands
            if subcommand in ['-f', '--file']:
                # File execution
                if len(command_parts) > 2:
                    file_arg = command_parts[2]
                    if os.path.isfile(file_arg):
                        files.append(file_arg)
            else:
                # General PHP execution, include source files
                source_files = self._analyze_php_sources(ctx.workspace_path)
                files.extend(source_files)
        
        # Analyze source dependencies using dependency analyzer
        entry_files = self._get_entry_files(ctx.workspace_path)
        if entry_files:
            analyzer = PHPDependencyAnalyzer(ctx.workspace_path)
            source_dependencies = analyzer.analyze_dependencies(entry_files)
            files.extend(list(source_dependencies))
        
        return BuildResult(
            files=files,
            globs=[],
            build_system=self.name,
            command_type=subcommand,
            confidence=0.9
        )
    
    def _get_entry_files(self, workspace_path: str) -> list[str]:
        """Get PHP entry files."""
        entry_files = []
        
        # Look for common entry points
        common_entries = [
            'index.php', 'bootstrap.php', 'app.php', 'start.php',
            'public/index.php', 'web/index.php', 'www/index.php'
        ]
        
        for entry in common_entries:
            entry_path = os.path.join(workspace_path, entry)
            if os.path.isfile(entry_path):
                entry_files.append(entry_path)
        
        # Look for composer autoload files
        composer_autoload = os.path.join(workspace_path, 'vendor', 'autoload.php')
        if os.path.isfile(composer_autoload):
            entry_files.append(composer_autoload)
        
        return entry_files
    
    def _find_php_configs(self, workspace_path: str) -> list[str]:
        """Find PHP project configuration files."""
        config_files = []
        
        # Composer configuration files
        composer_configs = [
            'composer.json', 'composer.lock', 'composer.phar',
            'composer-setup.php'
        ]
        
        for config in composer_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # PHP configuration files
        php_configs = [
            'phpunit.xml', 'phpunit.xml.dist', 'phpcs.xml', 'phpcs.xml.dist',
            'phpmd.xml', 'phpmd.xml.dist', 'phpstan.neon', 'psalm.xml',
            '.php-cs-fixer.php', '.php-cs-fixer.dist.php', '.phpcs.xml',
            'phpstan.neon', 'psalm.xml', '.editorconfig'
        ]
        
        for config in php_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # Web server configuration
        web_configs = [
            '.htaccess', 'web.config', 'nginx.conf', 'apache.conf',
            'index.php', 'public/index.php'
        ]
        
        for config in web_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        return config_files
    
    def _find_composer_files(self, workspace_path: str) -> list[str]:
        """Find Composer-specific files."""
        composer_files = []
        
        # Look for composer.json and composer.lock
        composer_json = os.path.join(workspace_path, 'composer.json')
        if os.path.isfile(composer_json):
            composer_files.append(composer_json)
            
            # Parse composer.json for additional information
            try:
                composer_data = self._parse_composer_json(composer_json)
                
                # Add autoload files
                if 'autoload' in composer_data:
                    autoload = composer_data['autoload']
                    if 'psr-4' in autoload:
                        for _namespace, path in autoload['psr-4'].items():
                            namespace_path = os.path.join(workspace_path, path)
                            if os.path.isdir(namespace_path):
                                composer_files.append(namespace_path)
                    
                    if 'psr-0' in autoload:
                        for _namespace, path in autoload['psr-0'].items():
                            namespace_path = os.path.join(workspace_path, path)
                            if os.path.isdir(namespace_path):
                                composer_files.append(namespace_path)
                
                # Add scripts
                if 'scripts' in composer_data:
                    scripts = composer_data['scripts']
                    for _script_name, script_content in scripts.items():
                        if isinstance(script_content, str) and 'php' in script_content.lower():
                            # Look for PHP files referenced in scripts
                            script_files = self._find_script_files(workspace_path, script_content)
                            composer_files.extend(script_files)
                            
            except Exception as e:
                logger.debug(f"Failed to parse composer.json: {e}")
        
        composer_lock = os.path.join(workspace_path, 'composer.lock')
        if os.path.isfile(composer_lock):
            composer_files.append(composer_lock)
        
        return composer_files
    
    def _parse_composer_json(self, composer_json_path: str) -> dict:
        """Parse composer.json without external dependencies."""
        try:
            with open(composer_json_path, encoding='utf-8') as f:
                content = f.read()
            
            # Simple JSON parsing for common fields
            composer_data = {}
            
            # Extract autoload information
            autoload_match = re.search(r'[\'"](autoload)[\'"]\s*:\s*({[^}]+})', content)
            if autoload_match:
                autoload_content = autoload_match.group(2)
                autoload = {}
                
                # Extract PSR-4 autoloading
                psr4_match = re.search(r'[\'"](psr-4)[\'"]\s*:\s*({[^}]+})', autoload_content)
                if psr4_match:
                    psr4_content = psr4_match.group(2)
                    psr4 = {}
                    psr4_matches = re.finditer(r'[\'"]([^\'"]+)[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]', psr4_content)
                    for match in psr4_matches:
                        psr4[match.group(1)] = match.group(2)
                    autoload['psr-4'] = psr4
                
                # Extract PSR-0 autoloading
                psr0_match = re.search(r'[\'"](psr-0)[\'"]\s*:\s*({[^}]+})', autoload_content)
                if psr0_match:
                    psr0_content = psr0_match.group(2)
                    psr0 = {}
                    psr0_matches = re.finditer(r'[\'"]([^\'"]+)[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]', psr0_content)
                    for match in psr0_matches:
                        psr0[match.group(1)] = match.group(2)
                    autoload['psr-0'] = psr0
                
                composer_data['autoload'] = autoload
            
            # Extract scripts
            scripts_match = re.search(r'[\'"](scripts)[\'"]\s*:\s*({[^}]+})', content)
            if scripts_match:
                scripts_content = scripts_match.group(2)
                scripts = {}
                script_matches = re.finditer(r'[\'"]([^\'"]+)[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]', scripts_content)
                for match in script_matches:
                    scripts[match.group(1)] = match.group(2)
                composer_data['scripts'] = scripts
            
            return composer_data
            
        except Exception as e:
            logger.debug(f"Failed to parse composer.json: {e}")
            return {}
    
    def _find_script_files(self, workspace_path: str, script_content: str) -> list[str]:
        """Find PHP files referenced in Composer scripts."""
        script_files = []
        
        # Look for common script patterns
        script_patterns = [
            r'php\s+([^\s]+\.php)',  # php script.php
            r'([^\s]+\.php)',  # script.php
            r'([^\s]+\.php)\s+',  # script.php args
        ]
        
        for pattern in script_patterns:
            matches = re.findall(pattern, script_content)
            for match in matches:
                script_path = os.path.join(workspace_path, match)
                if os.path.isfile(script_path):
                    script_files.append(script_path)
        
        return script_files
    
    def _analyze_php_sources(self, workspace_path: str) -> list[str]:
        """Analyze PHP source file dependencies."""
        files = []
        
        # Common PHP source directories
        source_dirs = ['src', 'app', 'main', 'core', 'api', 'services', 'controllers', 'models']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                files.append(source_path)
        
        # Add root PHP files
        try:
            root_php_files = [f for f in os.listdir(workspace_path) 
                             if f.endswith('.php') and os.path.isfile(os.path.join(workspace_path, f))]
            for php_file in root_php_files:
                files.append(os.path.join(workspace_path, php_file))
        except (OSError, PermissionError):
            pass
        
        # Look for index.php and bootstrap files
        common_entry_points = ['index.php', 'bootstrap.php', 'app.php', 'start.php']
        for entry_point in common_entry_points:
            entry_path = os.path.join(workspace_path, entry_point)
            if os.path.isfile(entry_path):
                files.append(entry_path)
        
        # Look for public/index.php (common in modern PHP frameworks)
        public_index = os.path.join(workspace_path, 'public', 'index.php')
        if os.path.isfile(public_index):
            files.append(public_index)
        
        return files
    
    def _find_php_test_files(self, workspace_path: str) -> list[str]:
        """Find PHP test files and directories."""
        test_items = []
        
        # Common test directories
        test_dirs = ['test', 'tests', 'testing', 'spec', 'Tests', 'tests/unit', 'tests/feature']
        for test_dir in test_dirs:
            test_path = os.path.join(workspace_path, test_dir)
            if os.path.isdir(test_path):
                test_items.append(test_path)
        
        # Look for test files
        import glob
        test_patterns = ['*Test.php', 'Test*.php', '*Tests.php', '*Spec.php', '*Test.php']
        for pattern in test_patterns:
            matches = glob.glob(os.path.join(workspace_path, '**', pattern), recursive=True)
            test_items.extend(matches)
        
        # Test configuration files
        test_configs = [
            'phpunit.xml', 'phpunit.xml.dist', 'phpcs.xml', 'phpcs.xml.dist',
            'phpmd.xml', 'phpmd.xml.dist', 'phpstan.neon', 'psalm.xml'
        ]
        for config in test_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                test_items.append(config_path)
        
        return test_items
    
    def _basic_analysis(self, ctx: BuildContext, subcommand: str) -> BuildResult:
        """Fallback to basic analysis when intelligent analysis fails."""
        files = []
        
        # Add common PHP files
        config_files = self._find_php_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Add key source files instead of broad patterns
        if subcommand in ['build', 'test', 'run', 'lint', 'format']:
            key_files = [
                'index.php', 'bootstrap.php', 'app.php', 'start.php',
                'composer.json', 'composer.lock'
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
