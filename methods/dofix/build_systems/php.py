"""
PHP build system detector with intelligent dependency analysis.
"""

import re
import os
import json
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class PHPBuildSystemDetector(BuildSystemDetector):
    """Intelligent detector for PHP build system commands."""
    
    @property
    def name(self) -> str:
        return "php"
    
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
        globs = []
        
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
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.9
        )
    
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
                with open(composer_json) as f:
                    composer_data = json.load(f)
                
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
                # Also add PHP files within these directories
                try:
                    for root, _dirs, filenames in os.walk(source_path):
                        for filename in filenames:
                            if filename.endswith('.php'):
                                file_path = os.path.join(root, filename)
                                if os.path.isfile(file_path):
                                    files.append(file_path)
                except (OSError, PermissionError):
                    pass
        
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
        globs = []
        
        # Add common PHP files
        config_files = self._find_php_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Add source file patterns
        if subcommand in ['build', 'test', 'run', 'lint', 'format']:
            globs.append(os.path.join(ctx.workspace_path, "**/*.php"))
            globs.append(os.path.join(ctx.workspace_path, "**/*.phtml"))  # PHP templates
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
