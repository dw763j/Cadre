"""
Java build system detector with intelligent dependency analysis.
"""

import re
import os
import xml.etree.ElementTree as ET
from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class JavaBuildSystemDetector(BuildSystemDetector):
    """Intelligent detector for Java build system commands."""
    
    @property
    def name(self) -> str:
        return "java"
    
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        if not command:
            return False
        
        # Match Java build tool commands
        java_patterns = [
            r'^\s*(mvn|mvnw)\s+\w+',  # Maven
            r'^\s*(gradle|gradlew)\s+\w+',  # Gradle
            r'^\s*(ant|antw)\s+\w+',  # Ant
            r'^\s*java\s+\w+',  # Java runtime
            r'^\s*javac\s+\w+',  # Java compiler
            r'^\s*junit\s+\w+'  # JUnit
        ]
        
        for pattern in java_patterns:
            if re.match(pattern, command, re.IGNORECASE):
                return True
        
        return False
    
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Intelligently analyze Java command and return files used from workspace."""
        command_parts = command.strip().split()
        if len(command_parts) < 2:
            raise ValueError(f"Invalid Java command: {command}")
        
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
        """Intelligently analyze Java project structure and dependencies."""
        files = []
        globs = []
        
        # Find and parse Java project configuration files
        config_files = self._find_java_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Analyze build dependencies based on tool and subcommand
        if tool in ['mvn', 'mvnw']:
            # Maven
            maven_files = self._analyze_maven_project(ctx.workspace_path, subcommand)
            files.extend(maven_files)
            
            # Analyze build dependencies
            build_files = self._analyze_build_dependencies(ctx.workspace_path)
            files.extend(build_files)
            
        elif tool in ['gradle', 'gradlew']:
            # Gradle
            gradle_files = self._analyze_gradle_project(ctx.workspace_path, subcommand)
            files.extend(gradle_files)
            
            # Analyze build dependencies
            build_files = self._analyze_build_dependencies(ctx.workspace_path)
            files.extend(build_files)
            
        elif tool in ['ant', 'antw']:
            # Ant
            ant_files = self._analyze_ant_project(ctx.workspace_path, subcommand)
            files.extend(ant_files)
            
        elif tool in ['java', 'javac']:
            # Java runtime/compiler
            java_files = self._analyze_java_sources(ctx.workspace_path)
            files.extend(java_files)
            
            # Analyze build dependencies
            build_files = self._analyze_build_dependencies(ctx.workspace_path)
            files.extend(build_files)
            
        elif tool == 'junit':
            # JUnit testing
            test_files = self._find_java_test_files(ctx.workspace_path)
            files.extend(test_files)
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.9
        )
    
    def _analyze_build_dependencies(self, workspace_path: str) -> list[str]:
        """Analyze files actually needed during the build."""
        files = []
        
        # 1. Parse build configuration in pom.xml
        pom_xml = os.path.join(workspace_path, 'pom.xml')
        if os.path.isfile(pom_xml):
            maven_files = self._parse_pom_xml_build_config(pom_xml, workspace_path)
            files.extend(maven_files)
        
        # 2. Parse build.gradle
        build_gradle = os.path.join(workspace_path, 'build.gradle')
        if os.path.isfile(build_gradle):
            gradle_files = self._parse_build_gradle(build_gradle, workspace_path)
            files.extend(gradle_files)
        
        # 3. Analyze Java class import relationships
        import_files = self._analyze_java_imports(workspace_path)
        files.extend(import_files)
        
        return files
    
    def _parse_pom_xml_build_config(self, pom_xml_path: str, workspace_path: str) -> list[str]:
        """Parse build configuration in pom.xml."""
        files = []
        
        try:
            tree = ET.parse(pom_xml_path)
            root = tree.getroot()
            
            # Parse source directory
            build = root.find('.//{*}build')
            if build is not None:
                source_dir = build.find('.//{*}sourceDirectory')
                if source_dir is not None and source_dir.text:
                    source_path = os.path.join(workspace_path, source_dir.text)
                    if os.path.isdir(source_path):
                        files.append(source_path)
                        # Recursively add all Java files
                        for root_dir, _, filenames in os.walk(source_path):
                            for filename in filenames:
                                if filename.endswith('.java'):
                                    files.append(os.path.join(root_dir, filename))
                
                # Parse test source directory
                test_source_dir = build.find('.//{*}testSourceDirectory')
                if test_source_dir is not None and test_source_dir.text:
                    test_source_path = os.path.join(workspace_path, test_source_dir.text)
                    if os.path.isdir(test_source_path):
                        files.append(test_source_path)
                        # Recursively add all test Java files
                        for root_dir, _, filenames in os.walk(test_source_path):
                            for filename in filenames:
                                if filename.endswith('.java'):
                                    files.append(os.path.join(root_dir, filename))
            
            # Parse resource directories
            resources = root.findall('.//{*}resource')
            for resource in resources:
                directory = resource.find('.//{*}directory')
                if directory is not None and directory.text:
                    resource_path = os.path.join(workspace_path, directory.text)
                    if os.path.isdir(resource_path):
                        files.append(resource_path)
                        # Recursively add all resource files
                        for root_dir, _, filenames in os.walk(resource_path):
                            for filename in filenames:
                                files.append(os.path.join(root_dir, filename))
            
            # Parse plugin configuration
            plugins = root.findall('.//{*}plugin')
            for plugin in plugins:
                plugin_files = self._parse_plugin_config(plugin, workspace_path)
                files.extend(plugin_files)
        
        except Exception as e:
            logger.debug(f"Failed to parse pom.xml: {e}")
        
        return files
    
    def _parse_plugin_config(self, plugin: ET.Element, workspace_path: str) -> list[str]:
        """Parse Maven plugin configuration."""
        files = []
        
        try:
            # Find file paths in plugin configuration
            configuration = plugin.find('.//{*}configuration')
            if configuration is not None:
                # Find common file path settings
                for tag in ['sourceDirectory', 'outputDirectory', 'testSourceDirectory']:
                    tag_elem = configuration.find(f'.//{{{tag}}}')
                    if tag_elem is not None and tag_elem.text:
                        path = os.path.join(workspace_path, tag_elem.text)
                        if os.path.exists(path):
                            files.append(path)
        except Exception:
            pass
        
        return files
    
    def _parse_build_gradle(self, gradle_path: str, workspace_path: str) -> list[str]:
        """Parse build.gradle."""
        files = []
        
        try:
            with open(gradle_path) as f:
                content = f.read()
            
            # Find source directory configuration
            source_dir_patterns = [
                r'sourceSets\s*\{\s*main\s*\{\s*java\s*\{\s*srcDirs\s*=\s*\[(.*?)\]',
                r'sourceSets\s*\{\s*main\s*\{\s*java\s*\{\s*srcDir\s*["\']([^"\']+)["\']',
                r'sourceSets\s*\{\s*main\s*\{\s*java\s*\{\s*srcDirs\s*=\s*["\']([^"\']+)["\']'
            ]
            
            for pattern in source_dir_patterns:
                matches = re.findall(pattern, content, re.DOTALL)
                for match in matches:
                    if match.startswith('['):
                        # Handle array format
                        dirs = re.findall(r'["\']([^"\']+)["\']', match)
                        for dir_name in dirs:
                            dir_path = os.path.join(workspace_path, dir_name)
                            if os.path.isdir(dir_path):
                                files.append(dir_path)
                                # Recursively add all Java files in the directory
                                for root, _, filenames in os.walk(dir_path):
                                    for filename in filenames:
                                        if filename.endswith('.java'):
                                            files.append(os.path.join(root, filename))
                    else:
                        # Handle a single directory
                        dir_path = os.path.join(workspace_path, match)
                        if os.path.isdir(dir_path):
                            files.append(dir_path)
                            # Recursively add all Java files in the directory
                            for root, _, filenames in os.walk(dir_path):
                                for filename in filenames:
                                    if filename.endswith('.java'):
                                        files.append(os.path.join(root, filename))
        
        except Exception as e:
            logger.debug(f"Failed to parse build.gradle: {e}")
        
        return files
    
    def _analyze_java_imports(self, workspace_path: str) -> list[str]:
        """Analyze import relationships across Java files."""
        files = []
        import_graph = {}
        
        # Scan all Java files
        for root, _, filenames in os.walk(workspace_path):
            for filename in filenames:
                if filename.endswith('.java'):
                    file_path = os.path.join(root, filename)
                    imports = self._extract_java_imports(file_path)
                    if imports:
                        import_graph[file_path] = imports
        
        # Starting from main classes, recursively find dependencies
        main_classes = self._find_java_main_classes(workspace_path)
        for main_class in main_classes:
            dependent_files = self._find_java_dependent_files(main_class, import_graph, workspace_path)
            files.extend(dependent_files)
        
        return list(set(files))
    
    def _extract_java_imports(self, file_path: str) -> list[str]:
        """Extract import statements from a Java file."""
        imports = []
        
        try:
            with open(file_path) as f:
                content = f.read()
            
            # Extract import statements
            import_pattern = r'^import\s+([\w.]+);'
            matches = re.findall(import_pattern, content, re.MULTILINE)
            imports.extend(matches)
            
            # Extract static imports
            static_import_pattern = r'^import\s+static\s+([\w.]+);'
            static_matches = re.findall(static_import_pattern, content, re.MULTILINE)
            imports.extend(static_matches)
        
        except Exception:
            pass
        
        return imports
    
    def _find_java_main_classes(self, workspace_path: str) -> list[str]:
        """Find Java main classes."""
        main_classes = []
        
        # Find classes containing a main method
        for root, _, filenames in os.walk(workspace_path):
            for filename in filenames:
                if filename.endswith('.java'):
                    file_path = os.path.join(root, filename)
                    if self._contains_main_method(file_path):
                        main_classes.append(file_path)
        
        return main_classes
    
    def _contains_main_method(self, file_path: str) -> bool:
        """Check whether a Java file contains a main method."""
        try:
            with open(file_path) as f:
                content = f.read()
            
            # Find public static void main method
            main_pattern = r'public\s+static\s+void\s+main\s*\('
            return bool(re.search(main_pattern, content))
        except Exception:
            return False
    
    def _find_java_dependent_files(self, main_class: str, import_graph: dict, workspace_path: str) -> list[str]:
        """Recursively find dependent files."""
        files = [main_class]
        visited = set()
        
        def dfs(file_path: str):
            if file_path in visited:
                return
            visited.add(file_path)
            
            if file_path in import_graph:
                for import_name in import_graph[file_path]:
                    # Convert import name to file path
                    import_file = self._resolve_java_import(import_name, file_path, workspace_path)
                    if import_file and import_file not in visited:
                        files.append(import_file)
                        dfs(import_file)
        
        dfs(main_class)
        return files
    
    def _resolve_java_import(self, import_name: str, source_file: str, workspace_path: str) -> str | None:
        """Resolve a Java import name to a file path."""
        # Search for class files within the workspace
        for root, _, filenames in os.walk(workspace_path):
            for filename in filenames:
                if filename.endswith('.java'):
                    # Check whether the package path matches
                    relative_path = os.path.relpath(os.path.join(root, filename), workspace_path)
                    package_path = relative_path.replace(os.sep, '.').replace('.java', '')
                    
                    if package_path == import_name:
                        return os.path.join(root, filename)
        
        return None
    
    def _find_java_configs(self, workspace_path: str) -> list[str]:
        """Find Java project configuration files."""
        config_files = []
        
        # Maven configuration files
        maven_configs = ['pom.xml', '.mvn/wrapper/maven-wrapper.properties']
        for config in maven_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # Gradle configuration files
        gradle_configs = [
            'build.gradle', 'build.gradle.kts', 'settings.gradle',
            'settings.gradle.kts', 'gradle.properties', 'gradle/wrapper/gradle-wrapper.properties'
        ]
        for config in gradle_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # Ant configuration files
        ant_configs = ['build.xml', 'build.properties']
        for config in ant_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        # IDE and other configuration files
        ide_configs = [
            '.classpath', '.project', '.factorypath',
            'eclipse.ini', 'idea.properties'
        ]
        for config in ide_configs:
            config_path = os.path.join(workspace_path, config)
            if os.path.isfile(config_path):
                config_files.append(config_path)
        
        return config_files
    
    def _analyze_maven_project(self, workspace_path: str, subcommand: str) -> list[str]:
        """Analyze Maven project structure and dependencies."""
        files = []
        
        pom_xml = os.path.join(workspace_path, 'pom.xml')
        if not os.path.isfile(pom_xml):
            return files
        
        try:
            # Parse pom.xml
            tree = ET.parse(pom_xml)
            root = tree.getroot()
            
            # Extract project information
            # group_id = root.find('.//{*}groupId')
            # artifact_id = root.find('.//{*}artifactId')
            # packaging = root.find('.//{*}packaging')
            
            # Find source directories
            source_dirs = self._find_maven_source_dirs(root, workspace_path)
            files.extend(source_dirs)
            
            # Find test directories
            if subcommand in ['test', 'verify']:
                test_dirs = self._find_maven_test_dirs(root, workspace_path)
                files.extend(test_dirs)
            
            # Find resource directories
            resource_dirs = self._find_maven_resource_dirs(root, workspace_path)
            files.extend(resource_dirs)
            
        except Exception as e:
            logger.debug(f"Failed to parse pom.xml: {e}")
        
        return files
    
    def _find_maven_source_dirs(self, root: ET.Element, workspace_path: str) -> list[str]:
        """Find Maven source directories from pom.xml."""
        source_dirs = []
        
        # Default Maven directory structure
        default_src = os.path.join(workspace_path, 'src', 'main', 'java')
        if os.path.isdir(default_src):
            source_dirs.append(default_src)
        
        # Check for custom source directory configuration
        build = root.find('.//{*}build')
        if build is not None:
            source_dir = build.find('.//{*}sourceDirectory')
            if source_dir is not None and source_dir.text:
                custom_src = os.path.join(workspace_path, source_dir.text)
                if os.path.isdir(custom_src):
                    source_dirs.append(custom_src)
        
        return source_dirs
    
    def _find_maven_test_dirs(self, root: ET.Element, workspace_path: str) -> list[str]:
        """Find Maven test directories from pom.xml."""
        test_dirs = []
        
        # Default Maven test directory structure
        default_test = os.path.join(workspace_path, 'src', 'test', 'java')
        if os.path.isdir(default_test):
            test_dirs.append(default_test)
        
        # Check for custom test directory configuration
        build = root.find('.//{*}build')
        if build is not None:
            test_dir = build.find('.//{*}testSourceDirectory')
            if test_dir is not None and test_dir.text:
                custom_test = os.path.join(workspace_path, test_dir.text)
                if os.path.isdir(custom_test):
                    test_dirs.append(custom_test)
        
        return test_dirs
    
    def _find_maven_resource_dirs(self, root: ET.Element, workspace_path: str) -> list[str]:
        """Find Maven resource directories from pom.xml."""
        resource_dirs = []
        
        # Default Maven resource directory structure
        default_resources = os.path.join(workspace_path, 'src', 'main', 'resources')
        if os.path.isdir(default_resources):
            resource_dirs.append(default_resources)
        
        default_test_resources = os.path.join(workspace_path, 'src', 'test', 'resources')
        if os.path.isdir(default_test_resources):
            resource_dirs.append(default_test_resources)
        
        return resource_dirs
    
    def _analyze_gradle_project(self, workspace_path: str, subcommand: str) -> list[str]:
        """Analyze Gradle project structure and dependencies."""
        files = []
        
        # Common Gradle source directories
        gradle_src_dirs = [
            'src/main/java', 'src/main/kotlin', 'src/main/groovy',
            'src/test/java', 'src/test/kotlin', 'src/test/groovy'
        ]
        
        for src_dir in gradle_src_dirs:
            src_path = os.path.join(workspace_path, src_dir)
            if os.path.isdir(src_path):
                files.append(src_path)
        
        # Look for build.gradle files
        build_gradle_files = ['build.gradle', 'build.gradle.kts']
        for build_file in build_gradle_files:
            build_path = os.path.join(workspace_path, build_file)
            if os.path.isfile(build_path):
                files.append(build_path)
        
        return files
    
    def _analyze_ant_project(self, workspace_path: str, subcommand: str) -> list[str]:
        """Analyze Ant project structure and dependencies."""
        files = []
        
        # Common Ant source directories
        ant_src_dirs = ['src', 'source', 'java']
        for src_dir in ant_src_dirs:
            src_path = os.path.join(workspace_path, src_dir)
            if os.path.isdir(src_path):
                files.append(src_path)
        
        # Look for build.xml
        build_xml = os.path.join(workspace_path, 'build.xml')
        if os.path.isfile(build_xml):
            files.append(build_xml)
        
        return files
    
    def _analyze_java_sources(self, workspace_path: str) -> list[str]:
        """Analyze Java source files and directories."""
        files = []
        
        # Common Java source directories
        source_dirs = ['src', 'source', 'main', 'java']
        for source_dir in source_dirs:
            source_path = os.path.join(workspace_path, source_dir)
            if os.path.isdir(source_path):
                files.append(source_path)
                # Also add Java files within these directories
                try:
                    for root, _dirs, filenames in os.walk(source_path):
                        for filename in filenames:
                            if filename.endswith('.java'):
                                file_path = os.path.join(root, filename)
                                if os.path.isfile(file_path):
                                    files.append(file_path)
                except (OSError, PermissionError):
                    pass
        
        # Add root Java files
        try:
            root_java_files = [f for f in os.listdir(workspace_path) 
                               if f.endswith('.java') and os.path.isfile(os.path.join(workspace_path, f))]
            for java_file in root_java_files:
                files.append(os.path.join(workspace_path, java_file))
        except (OSError, PermissionError):
            pass  # Skip if we can't list directory
        
        return files
    
    def _find_java_test_files(self, workspace_path: str) -> list[str]:
        """Find Java test files and directories."""
        test_items = []
        
        # Common test directories
        test_dirs = ['test', 'tests', 'src/test', 'src/tests']
        for test_dir in test_dirs:
            test_path = os.path.join(workspace_path, test_dir)
            if os.path.isdir(test_path):
                test_items.append(test_path)
        
        # Look for test files
        import glob
        test_patterns = ['*Test.java', 'Test*.java', '*Tests.java']
        for pattern in test_patterns:
            matches = glob.glob(os.path.join(workspace_path, '**', pattern), recursive=True)
            test_items.extend(matches)
        
        return test_items
    
    def _basic_analysis(self, ctx: BuildContext, subcommand: str) -> BuildResult:
        """Fallback to basic analysis when intelligent analysis fails."""
        files = []
        globs = []
        
        # Add common Java files
        config_files = self._find_java_configs(ctx.workspace_path)
        files.extend(config_files)
        
        # Add source file patterns
        if subcommand in ['build', 'test', 'run', 'compile']:
            globs.append(os.path.join(ctx.workspace_path, "**/*.java"))
            globs.append(os.path.join(ctx.workspace_path, "**/*.kt"))  # Kotlin
            globs.append(os.path.join(ctx.workspace_path, "**/*.groovy"))  # Groovy
        
        return BuildResult(
            files=files,
            globs=globs,
            build_system=self.name,
            command_type=subcommand,
            confidence=0.7
        )
