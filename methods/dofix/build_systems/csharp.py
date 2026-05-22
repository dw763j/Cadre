"""
C# (.NET) build system detector with intelligent dependency analysis.
"""

import re
import os
import xml.etree.ElementTree as ET
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class CSharpBuildSystemDetector(BuildSystemDetector):
    """Intelligent detector for C# (.NET) build system commands."""
    
    @property
    def name(self) -> str:
        return "csharp"
    
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        if not command:
            return False
        
        # Match C# build tool commands
        csharp_patterns = [
            r'^\s*dotnet\s+\w+',  # .NET CLI
            r'^\s*msbuild\s+\w+',  # MSBuild
            r'^\s*nuget\s+\w+',  # NuGet
            r'^\s*csc\s+\w+',  # C# compiler
            r'^\s*roslyn\s+\w+'  # Roslyn compiler
        ]
        
        for pattern in csharp_patterns:
            if re.match(pattern, command, re.IGNORECASE):
                return True
        
        return False
    
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Intelligently analyze C# command and return files used from workspace."""
        command_parts = command.strip().split()
        if len(command_parts) < 2:
            raise ValueError(f"Invalid C# command: {command}")
        
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
        """Intelligently analyze C# project structure and dependencies."""
        files = []
        globs = []
        
        # Find and parse C# project configuration files
        config_files = self._find_csharp_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Analyze based on tool and subcommand
        if tool == 'dotnet':
            if subcommand in ['build', 'run', 'publish']:
                # For build/run commands, include source files
                source_files = self._analyze_dotnet_sources(ctx.workspace_path)
                files.extend(source_files)
                
            elif subcommand == 'test':
                # For test commands, include test files
                test_files = self._find_csharp_test_files(ctx.workspace_path)
                files.extend(test_files)
                
                # Also include source files for testing
                source_files = self._analyze_dotnet_sources(ctx.workspace_path)
                files.extend(source_files)
                
            elif subcommand == 'restore':
                # For restore, focus on project files
                project_files = self._find_project_files(ctx.workspace_path)
                files.extend(project_files)
                
        elif tool == 'msbuild':
            # MSBuild commands
            if subcommand in ['build', 'rebuild']:
                source_files = self._analyze_dotnet_sources(ctx.workspace_path)
                files.extend(source_files)
                
        elif tool == 'nuget':
            # NuGet commands
            if subcommand in ['restore', 'install']:
                project_files = self._find_project_files(ctx.workspace_path)
                files.extend(project_files)
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.9
        )
    
    def _find_csharp_configs(self, workspace_path: str) -> list[str]:
        """Find C# project configuration files."""
        config_files = []
        
        # .NET project files
        project_files = [
            '*.csproj', '*.vbproj', '*.fsproj',  # C#, VB.NET, F#
            '*.sln', '*.csproj.user', '*.vbproj.user'
        ]
        
        import glob
        for pattern in project_files:
            matches = glob.glob(os.path.join(workspace_path, pattern))
            config_files.extend(matches)
        
        # NuGet configuration files
        nuget_configs = [
            'nuget.config', 'packages.config', 'packages.lock.json',
            'global.json', 'Directory.Build.props', 'Directory.Build.targets'
        ]
        
        for config in nuget_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # .NET configuration files
        dotnet_configs = [
            'launchSettings.json', 'appsettings.json', 'appsettings.*.json',
            'web.config', 'app.config'
        ]
        
        for config in dotnet_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        return config_files
    
    def _find_project_files(self, workspace_path: str) -> list[str]:
        """Find C# project files."""
        project_files = []
        
        import glob
        # Look for project files
        project_patterns = ['*.csproj', '*.vbproj', '*.fsproj', '*.sln']
        for pattern in project_patterns:
            matches = glob.glob(os.path.join(workspace_path, pattern))
            project_files.extend(matches)
        
        return project_files
    
    def _analyze_dotnet_sources(self, workspace_path: str) -> list[str]:
        """Analyze .NET source file dependencies."""
        files = []
        
        # Common .NET source directories
        source_dirs = ['src', 'app', 'main', 'core', 'api', 'services', 'controllers']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                files.append(source_path)
                # Also add C# files within these directories
                try:
                    for root, _dirs, filenames in os.walk(source_path):
                        for filename in filenames:
                            if filename.endswith(('.cs', '.vb', '.fs')):
                                file_path = os.path.join(root, filename)
                                if os.path.isfile(file_path):
                                    files.append(file_path)
                except (OSError, PermissionError):
                    pass
        
        # Add root C# files
        try:
            root_cs_files = [f for f in os.listdir(workspace_path) 
                            if f.endswith(('.cs', '.vb', '.fs')) and os.path.isfile(os.path.join(workspace_path, f))]
            for cs_file in root_cs_files:
                files.append(os.path.join(workspace_path, cs_file))
        except (OSError, PermissionError):
            pass
        
        # Look for Program.cs and Startup.cs (common entry points)
        common_entry_points = ['Program.cs', 'Startup.cs', 'App.xaml.cs', 'MainWindow.xaml.cs']
        for entry_point in common_entry_points:
            entry_path = os.path.join(workspace_path, entry_point)
            if os.path.isfile(entry_path):
                files.append(entry_path)
        
        # Look for project-specific source directories
        project_files = self._find_project_files(workspace_path)
        for project_file in project_files:
            if project_file.endswith('.csproj'):
                project_sources = self._analyze_csproj_sources(project_file)
                files.extend(project_sources)
        
        return files
    
    def _analyze_csproj_sources(self, csproj_path: str) -> list[str]:
        """Analyze .csproj file for source files and directories."""
        source_files = []
        
        try:
            tree = ET.parse(csproj_path)
            root = tree.getroot()
            
            # Find source file references
            for item_group in root.findall('.//{*}ItemGroup'):
                for compile in item_group.findall('.//{*}Compile'):
                    include = compile.get('Include')
                    if include:
                        # Resolve relative path
                        project_dir = os.path.dirname(csproj_path)
                        source_path = os.path.join(project_dir, include)
                        if os.path.isfile(source_path):
                            source_files.append(source_path)
                
                # Find embedded resources
                for embedded_resource in item_group.findall('.//{*}EmbeddedResource'):
                    include = embedded_resource.get('Include')
                    if include:
                        project_dir = os.path.dirname(csproj_path)
                        resource_path = os.path.join(project_dir, include)
                        if os.path.isfile(resource_path):
                            source_files.append(resource_path)
                            
        except Exception as e:
            logger.debug(f"Failed to parse .csproj: {e}")
        
        return source_files
    
    def _find_csharp_test_files(self, workspace_path: str) -> list[str]:
        """Find C# test files and directories."""
        test_items = []
        
        # Common test directories
        test_dirs = ['test', 'tests', 'testing', 'spec', 'Tests']
        for test_dir in test_dirs:
            test_path = os.path.join(workspace_path, test_dir)
            if os.path.isdir(test_path):
                test_items.append(test_path)
        
        # Look for test files
        import glob
        test_patterns = ['*Test.cs', 'Test*.cs', '*Tests.cs', '*Spec.cs']
        for pattern in test_patterns:
            matches = glob.glob(os.path.join(workspace_path, '**', pattern), recursive=True)
            test_items.extend(matches)
        
        # Test configuration files
        test_configs = [
            'testsettings', 'testsettings.local', 'vstest.runsettings',
            'xunit.runner.json', 'nunit.framework.dll.config'
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
        
        # Add common C# files
        config_files = self._find_csharp_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Add source file patterns
        if subcommand in ['build', 'test', 'run', 'compile']:
            globs.append(os.path.join(ctx.workspace_path, "**/*.cs"))
            globs.append(os.path.join(ctx.workspace_path, "**/*.vb"))  # VB.NET
            globs.append(os.path.join(ctx.workspace_path, "**/*.fs"))  # F#
            globs.append(os.path.join(ctx.workspace_path, "**/*.xaml"))  # XAML
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
