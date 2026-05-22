"""
Ruby build system detector with intelligent dependency analysis.
"""

import re
import os
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class RubyBuildSystemDetector(BuildSystemDetector):
    """Intelligent detector for Ruby build system commands."""
    
    @property
    def name(self) -> str:
        return "ruby"
    
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        if not command:
            return False
        
        # Match Ruby build tool commands
        ruby_patterns = [
            r'^\s*bundle\s+\w+',  # Bundler
            r'^\s*rake\s+\w+',  # Rake
            r'^\s*ruby\s+\w+',  # Ruby runtime
            r'^\s*rspec\s+\w+',  # RSpec testing
            r'^\s*minitest\s+\w+',  # Minitest
            r'^\s*rubocop\s+\w+',  # RuboCop linting
            r'^\s*gem\s+\w+'  # RubyGems
        ]
        
        for pattern in ruby_patterns:
            if re.match(pattern, command, re.IGNORECASE):
                return True
        
        return False
    
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Intelligently analyze Ruby command and return files used from workspace."""
        command_parts = command.strip().split()
        if len(command_parts) < 2:
            raise ValueError(f"Invalid Ruby command: {command}")
        
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
        """Intelligently analyze Ruby project structure and dependencies."""
        files = []
        globs = []
        
        # Find and parse Ruby project configuration files
        config_files = self._find_ruby_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Analyze based on tool and subcommand
        if tool == 'bundle':
            if subcommand in ['install', 'update', 'add', 'remove']:
                # For dependency management, focus on Gemfile
                gemfile_files = self._find_gemfile_files(ctx.workspace_path)
                files.extend(gemfile_files)
                
            elif subcommand in ['exec', 'exec-command']:
                # For bundle exec, include source files
                source_files = self._analyze_ruby_sources(ctx.workspace_path)
                files.extend(source_files)
                
        elif tool == 'rake':
            # Rake commands
            if subcommand in ['build', 'compile', 'assets:precompile']:
                source_files = self._analyze_ruby_sources(ctx.workspace_path)
                files.extend(source_files)
                
            elif subcommand in ['test', 'spec', 'rspec']:
                test_files = self._find_ruby_test_files(ctx.workspace_path)
                files.extend(test_files)
                
                # Also include source files for testing
                source_files = self._analyze_ruby_sources(ctx.workspace_path)
                files.extend(source_files)
                
        elif tool in ['rspec', 'minitest']:
            # Testing tools
            test_files = self._find_ruby_test_files(ctx.workspace_path)
            files.extend(test_files)
            
            # Include source files for testing
            source_files = self._analyze_ruby_sources(ctx.workspace_path)
            files.extend(source_files)
            
        elif tool in ['rubocop', 'ruby-lint']:
            # Linting tools
            source_files = self._analyze_ruby_sources(ctx.workspace_path)
            files.extend(source_files)
            
        elif tool == 'ruby':
            # Ruby runtime commands
            if subcommand in ['-e', '--eval']:
                # Inline code execution
                pass
            else:
                # File execution, include source files
                source_files = self._analyze_ruby_sources(ctx.workspace_path)
                files.extend(source_files)
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.9
        )
    
    def _find_ruby_configs(self, workspace_path: str) -> list[str]:
        """Find Ruby project configuration files."""
        config_files = []
        
        # Ruby project files
        ruby_configs = [
            'Gemfile', 'Gemfile.lock', 'Rakefile', 'rakefile',
            'config.ru', 'config/application.rb', 'config/environment.rb'
        ]
        
        for config in ruby_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # Ruby version files
        version_files = ['.ruby-version', '.ruby-gemset', '.rvmrc']
        for version_file in version_files:
            version_path = os.path.join(workspace_path, version_file)
            if os.path.isfile(version_path):
                config_files.append(version_path)
        
        # Rails configuration files
        rails_configs = [
            'config/database.yml', 'config/routes.rb', 'config/initializers',
            'config/environments', 'config/locales'
        ]
        
        for config in rails_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isdir(config_path) or os.path.isfile(config_path):
                config_files.append(config_path)
        
        # Test configuration files
        test_configs = [
            '.rspec', 'spec/spec_helper.rb', 'test/test_helper.rb',
            'minitest_helper.rb'
        ]
        
        for config in test_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        return config_files
    
    def _find_gemfile_files(self, workspace_path: str) -> list[str]:
        """Find Gemfile and related files."""
        gemfile_files = []
        
        # Look for Gemfile
        gemfile_path = os.path.join(workspace_path, 'Gemfile')
        if os.path.isfile(gemfile_path):
            gemfile_files.append(gemfile_path)
            
            # Parse Gemfile for additional information
            try:
                with open(gemfile_path) as f:
                    content = f.read()
                
                # Look for require statements
                require_patterns = [
                    r"require\s+['\"]([^'\"]+)['\"]",
                    r"require_relative\s+['\"]([^'\"]+)['\"]"
                ]
                
                for pattern in require_patterns:
                    matches = re.findall(pattern, content)
                    for match in matches:
                        if match.endswith('.rb'):
                            require_path = os.path.join(workspace_path, match)
                            if os.path.isfile(require_path):
                                gemfile_files.append(require_path)
                        else:
                            # Try with .rb extension
                            require_path = os.path.join(workspace_path, f"{match}.rb")
                            if os.path.isfile(require_path):
                                gemfile_files.append(require_path)
                                
            except Exception as e:
                logger.debug(f"Failed to parse Gemfile: {e}")
        
        # Add Gemfile.lock
        gemfile_lock = os.path.join(workspace_path, 'Gemfile.lock')
        if os.path.isfile(gemfile_lock):
            gemfile_files.append(gemfile_lock)
        
        return gemfile_files
    
    def _analyze_ruby_sources(self, workspace_path: str) -> list[str]:
        """Analyze Ruby source file dependencies."""
        files = []
        
        # Common Ruby source directories
        source_dirs = ['lib', 'app', 'src', 'main', 'core', 'models', 'controllers', 'helpers']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                files.append(source_path)
                # Also add Ruby files within these directories
                try:
                    for root, _dirs, filenames in os.walk(source_path):
                        for filename in filenames:
                            if filename.endswith('.rb'):
                                file_path = os.path.join(root, filename)
                                if os.path.isfile(file_path):
                                    files.append(file_path)
                except (OSError, PermissionError):
                    pass
        
        # Add root Ruby files
        try:
            root_rb_files = [f for f in os.listdir(workspace_path) 
                             if f.endswith('.rb') and os.path.isfile(os.path.join(workspace_path, f))]
            for rb_file in root_rb_files:
                files.append(os.path.join(workspace_path, rb_file))
        except (OSError, PermissionError):
            pass
        
        # Look for common entry points
        common_entry_points = ['main.rb', 'app.rb', 'application.rb', 'server.rb']
        for entry_point in common_entry_points:
            entry_path = os.path.join(workspace_path, entry_point)
            if os.path.isfile(entry_path):
                files.append(entry_path)
        
        # Look for Rails application files
        rails_files = [
            'config/application.rb', 'config/environment.rb',
            'config/routes.rb', 'config/database.yml'
        ]
        
        for rails_file in rails_files:
            rails_path = os.path.join(workspace_path, rails_file)
            if os.path.isfile(rails_path):
                files.append(rails_path)
        
        return files
    
    def _find_ruby_test_files(self, workspace_path: str) -> list[str]:
        """Find Ruby test files and directories."""
        test_items = []
        
        # Common test directories
        test_dirs = ['test', 'tests', 'spec', 'tests/unit', 'tests/integration', 'spec/unit', 'spec/integration']
        for test_dir in test_dirs:
            test_path = os.path.join(workspace_path, test_dir)
            if os.path.isdir(test_path):
                test_items.append(test_path)
                # Also add test files within these directories
                try:
                    for root, _dirs, filenames in os.walk(test_path):
                        for filename in filenames:
                            if filename.endswith(('.rb', '_test.rb', 'test_*.rb')):
                                file_path = os.path.join(root, filename)
                                if os.path.isfile(file_path):
                                    test_items.append(file_path)
                except (OSError, PermissionError):
                    pass
        
        # Look for test files
        import glob
        test_patterns = ['*_test.rb', 'test_*.rb', '*_spec.rb', 'spec_*.rb']
        for pattern in test_patterns:
            matches = glob.glob(os.path.join(workspace_path, '**', pattern), recursive=True)
            test_items.extend(matches)
        
        # Test configuration files
        test_configs = [
            '.rspec', 'spec/spec_helper.rb', 'test/test_helper.rb',
            'minitest_helper.rb', 'test_helper.rb'
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
        
        # Add common Ruby files
        config_files = self._find_ruby_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Add source file patterns
        if subcommand in ['build', 'test', 'run', 'lint', 'format']:
            globs.append(os.path.join(ctx.workspace_path, "**/*.rb"))
            globs.append(os.path.join(ctx.workspace_path, "**/*.rake"))  # Rake files
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
