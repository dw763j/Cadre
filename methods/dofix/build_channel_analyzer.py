# 1. Parse the Dockerfile command-by-command with dockerfile-parser
# 2. For RUN, CMD, SHELL, and ENTRYPOINT, extract execution content and record it
# 3. Maintain a path list for each command's current file path (repo path and container path)
# 4. Maintain file relationships for files copied into the container (repo path and container path)
# 5. Track file dependencies per command; for explicitly used files, trace related files in the repo

# Run: find . -name "*.pyc" -delete && find . -name "__pycache__" -type d -exec rm -rf {} + && python -m methods.dofix.build_channel_analyzer xxx

import re
import os
import json
import shlex
import traceback
from dockerfile_parse import DockerfileParser
from loguru import logger
from pathlib import Path
from methods.dofix.VariableResolver import VariableResolver
from methods.dofix.PathManager import PathManager
from methods.dofix.build_systems.manager import BuildSystemManager
# from methods.dofix.mappers.container_to_repo import ContainerToRepoMapper


class BuildChannelAnalyzer:
    """
    Analyzes Dockerfile build instructions to track file dependencies and command executions.
    """
    
    # Class constants
    DEFAULT_WORKDIR = '/'
    SUPPORTED_INSTRUCTIONS = ['FROM', 'WORKDIR', 'ENV', 'ARG', 'COPY', 'ADD', 'RUN', 'CMD', 
    'ENTRYPOINT', 'SHELL', 'USER', 'VOLUME', 'EXPOSE', 'HEALTHCHECK', 'LABEL', 'COMMENT']
    DEFAULT_PATH = ['/usr/local/sbin', '/usr/local/bin', '/usr/sbin', '/usr/bin', '/sbin', '/bin']
    COMMAND_SPLITTERS = ['&&', ';']
    CD_COMMAND_PATTERN = r'^\s*cd\s+([^\s]+)(?:\s+|$)'
    FILE_OP_PATTERN = r'^\s*(mv|cp)\s+(-r\s+)?([^\s]+)\s+([^\s]+)(?:\s+|$)'
    
    def __init__(self, dockerfile_path, workspace_path, base_image_info=None, full_build_id=None):
        self.dockerfile_path = str(dockerfile_path)
        self.workspace_path = str(workspace_path)
        self.full_build_id = full_build_id

        self.dfp = DockerfileParser(path=str(dockerfile_path))
        self.dockerignore = self._parse_dockerignore(str(Path(workspace_path).joinpath('.dockerignore')))
        self.env_from_file = self._parse_env_from_file(str(Path(workspace_path).joinpath('.env')))

        self.current_workdir = self.DEFAULT_WORKDIR  # Default WORKDIR in a container
        self.file_dependencies = [] # To store tuples of (repo_path, container_path, command_type, command_content)
        self.command_executions = [] # To store details about command executions

        self.all_commands = {} # To store all commands
        self.all_files = {} # To store all files
        self.current_command_index = 0 # To store the index of the current command
        # self.current_file_index = 0 # To store the index of the current file

        # Multi-stage build support
        self.stages = {}  # Store info about each build stage
        self.current_stage = None  # Current stage name/id
        self.stage_files = {}  # Files available in each stage
        
        # Default PATH if no base image info is provided
        self.default_path = self.DEFAULT_PATH.copy()
        
        # Base image information
        self.base_image_info = base_image_info or {}
        self.stage_base_images = {}  # Base image for each stage
        self.stage_paths = {}  # Path for each stage
        
        # ARG and ENV variable support
        self.global_args = {}  # ARG variables available across all stages
        self.stage_args = {}    # ARG variables specific to each stage
        
        # Initialize managers
        self._path_manager = PathManager(self.default_path)
        self._variable_resolver = None
        self._update_variable_resolver()
        
        # Initialize build system manager
        self._build_system_manager = BuildSystemManager()
    

    def _add_command(self, command):
        """Add a command to the all_commands dictionary"""
        self.all_commands[self.current_command_index] = {
            'type': command['type'], # str
            'value': command['value'], # str
            'workdir': command['workdir'], # str
            'stage': command['stage'], # str
            'files': command['files'], # list
            'index': self.current_command_index # int
        }
        self.current_command_index += 1

    def _add_file(self, file):
        """Add a file to the all_files dictionary"""
        for key, value in file.items():
            if isinstance(value, str) and self.workspace_path in value:
                file[key] = value.replace(self.workspace_path, ".")
        if file['path_from_repo'] not in self.all_files:
            self.all_files[file['path_from_repo']] = []

        self.all_files[file['path_from_repo']].append({
            'source': file['source'], # str
            'source_path': file['source_path'], # str
            'destination': file['destination'], # str
            'destination_path': file['destination_path'], # str
            'command_index': file['command_index'] # int
        })
        return file['path_from_repo']

    def _update_variable_resolver(self):
        """Update the variable resolver with current state"""
        self._variable_resolver = VariableResolver(
            self.global_args, 
            self.stage_args, 
            self.env_from_file, 
            self.stages, 
            self.current_stage
        )

    def _parse_env_from_file(self, env_from_file_path):
        """
        Parse .env file to get list of environment variables.
        Handles quoted values and comments safely.
        """
        if not Path(env_from_file_path).exists() or self._should_ignore_file(env_from_file_path):
            return {}

        env_vars = {}
        try:
            with open(env_from_file_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Handle quoted values
                    if '=' in line:
                        try:
                            # Split on first '=' only
                            key, value = line.split('=', 1)
                            key = key.strip()
                            
                            # Remove surrounding quotes if present
                            if value.startswith('"') and value.endswith('"'):
                                value = value[1:-1]
                            elif value.startswith("'") and value.endswith("'"):
                                value = value[1:-1]
                            
                            if key:  # Only add if key is not empty
                                env_vars[key] = value
                        except ValueError:
                            # Skip malformed lines
                            continue
        except OSError as e:
            # Log error but don't fail completely
            print(f"Warning: Could not read .env file {env_from_file_path}: {e}")
        
        return env_vars

    def _parse_dockerignore(self, dockerignore_path):
        """
        Parse .dockerignore file to get list of ignored patterns.
        Handles comments and empty lines safely.
        """
        if not Path(dockerignore_path).exists():
            return []
        
        patterns = []
        try:
            with open(dockerignore_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Add valid pattern
                    patterns.append(line)
        except OSError as e:
            # Log error but don't fail completely
            print(f"Warning: Could not read .dockerignore file {dockerignore_path}: {e}")
        
        return patterns

    def _should_ignore_file(self, file_path):
        """
        Check if a file should be ignored based on .dockerignore patterns.
        Returns True if the file should be ignored, False otherwise.
        """
        if not self.dockerignore:
            return False
            
        # Get relative path from workspace
        try:
            rel_path = Path(file_path).relative_to(self.workspace_path)
        except ValueError:
            # If file_path is not relative to workspace, don't ignore
            return False
            
        # Normalize to use forward slashes for consistency
        rel_path = str(rel_path).replace(os.sep, '/')
        
        for pattern in self.dockerignore:
            # Skip comments and empty lines
            if pattern.startswith('#') or not pattern:
                continue
                
            # Handle negation patterns (starting with !)
            if pattern.startswith('!'):
                negated_pattern = pattern[1:]
                # For negation, we need to check if the file matches the negated pattern
                if self._matches_pattern(rel_path, negated_pattern):
                    return False
                continue
            
            # Check if file matches the pattern
            if self._matches_pattern(rel_path, pattern):
                # logger.debug(f"Ignoring file: {rel_path} by pattern: {pattern}")
                return True
                
        return False
    
    def _matches_pattern(self, file_path, pattern):
        """
        Check if a file path matches a .dockerignore pattern.
        Handles glob patterns like *, **, ?, etc. more accurately.
        """
        import fnmatch
        
        # Normalize pattern to use forward slashes
        pattern = pattern.replace('\\', '/')
        file_path = str(file_path).replace(os.sep, '/')
        
        # Handle special cases
        if pattern == '.' or pattern == './':
            return file_path == '.' or file_path == './'
        
        # Handle ** pattern for recursive matching
        if '**' in pattern:
            # Convert ** to appropriate glob pattern
            # **/ matches zero or more directories
            # /** matches zero or more directories at the end
            if pattern.startswith('**/'):
                # **/file matches file in any subdirectory
                pattern = pattern[3:]  # Remove **/
                if not pattern:  # **/ alone matches everything
                    return True
                # Check if file_path ends with the pattern
                return file_path.endswith(pattern) or fnmatch.fnmatch(file_path, f"*/{pattern}")
            elif pattern.endswith('/**'):
                # dir/** matches dir and all its contents
                base_pattern = pattern[:-3]  # Remove /**
                return file_path == base_pattern or file_path.startswith(f"{base_pattern}/")
            else:
                # ** in the middle, convert to glob pattern
                pattern = pattern.replace('**', '*')
        
        # Handle negation patterns (starting with !)
        if pattern.startswith('!'):
            pattern = pattern[1:]
        
        # Use fnmatch for pattern matching
        return fnmatch.fnmatch(file_path, pattern)

    def _scan_directory(self, repo_path, container_base_path):
        """
        Recursively scan directory and return list of (repo_path, container_path) tuples.
        Excludes .git directory.
        """
        results = []
        if not Path(repo_path).exists():
            return results

        for root, dirs, files in os.walk(repo_path):
            # Skip .git directory
            if '.git' in dirs:
                dirs.remove('.git')
            
            # Calculate relative path from repo_path
            rel_path = Path(root).relative_to(repo_path)
            if rel_path == '.':
                rel_path = ''
            
            # Add files
            for file in files:
                repo_file_path = Path(root).joinpath(file)
                # Skip files that should be ignored by .dockerignore
                if self._should_ignore_file(repo_file_path):
                    continue
                container_file_path = Path(container_base_path).joinpath(rel_path, file)
                results.append((repo_file_path, container_file_path))
            
            # Add directories
            for dir_name in dirs:
                repo_dir_path = Path(root).joinpath(dir_name)
                # Skip directories that should be ignored by .dockerignore
                if self._should_ignore_file(repo_dir_path):
                    # logger.debug(f'Ignoring directory: {repo_dir_path}')
                    continue
                container_dir_path = Path(container_base_path).joinpath(rel_path, dir_name)
                results.append((repo_dir_path, container_dir_path))
        
        return results

    def _load_base_image_path(self, image_name):
        """
        Load PATH from base image information.
        Returns a list of directories in the PATH.
        """
        # Try to get base image info from provided data
        if image_name in self.base_image_info:
            image_data = self.base_image_info[image_name]
            
            # Extract PATH from environment variables
            if 'Config' in image_data and 'Env' in image_data['Config']:
                for env_var in image_data['Config']['Env']:
                    if env_var.startswith('PATH='):
                        path_value = env_var[5:]  # Remove 'PATH=' prefix
                        return path_value.split(':')
        
        # Default PATH if not found
        return self.default_path.copy()

    def _parse_from_instruction(self, command_value):
        """
        Parse FROM instruction with support for all options:
        - FROM base_image
        - FROM base_image AS stage_name
        - FROM --platform=platform base_image
        - FROM --platform=platform base_image AS stage_name
        """
        parts = command_value.split()
        if not parts:
            return
        
        # Handle platform-specific FROM
        platform = None
        base_image = None
        stage_name = None
        
        i = 0
        while i < len(parts):
            part = parts[i]
            
            # Handle --platform option
            if part.startswith('--platform='):
                platform = part[len('--platform='):].strip()
                i += 1
                continue
            
            # Handle --chown option (though not standard for FROM, some tools might use it)
            elif part.startswith('--chown='):
                # Skip chown option for FROM instruction
                i += 1
                continue
            
            # Handle other potential options
            elif part.startswith('--'):
                # Skip unknown options
                i += 1
                continue
            
            # This should be the base image
            elif not base_image:
                base_image = part
                i += 1
                
                # Look for AS clause
                if i < len(parts) and parts[i].upper() == 'AS':
                    i += 1
                    if i < len(parts):
                        stage_name = parts[i]
                        i += 1
                break
            
            else:
                i += 1
        
        # Resolve variable references in base_image and platform
        if base_image and '$' in base_image:
            base_image = self._parse_env_parameter(base_image)
        
        if platform and '$' in platform:
            platform = self._parse_env_parameter(platform)
        
        # Determine the actual stage name (either alias or generated)
        if stage_name:
            self.current_stage = stage_name
        else:
            self.current_stage = f"stage-{len(self.stages)}"

        # Initialize stage properties
        self.stages[self.current_stage] = {
            'workdir': self.DEFAULT_WORKDIR, # Default workdir for a new stage
            'files': [], # Files will be added by COPY/ADD
            'base_image': base_image,
            'platform': platform
        }
        self.stage_files[self.current_stage] = []
        self.stage_base_images[self.current_stage] = base_image
        
        # Determine PATH for the new stage
        if base_image in self.stages:
            self.stage_paths[self.current_stage] = self.stage_paths.get(base_image, self.default_path.copy()).copy()
            self.stages[self.current_stage]['workdir'] = self.stages[base_image].get('workdir', '/')
        else:
            self.stage_paths[self.current_stage] = self._load_base_image_path(base_image)
        
        # Set current context for this new stage
        self.current_workdir = self.stages[self.current_stage]['workdir']
        self._path_manager.set_current_path(self.stage_paths[self.current_stage].copy())
        
        # Update variable resolver with current state
        self._update_variable_resolver()

    def _parse_global_arg_instruction(self, command_value):
        """
        Parse global ARG instruction (before first FROM).
        These ARGs are available across all build stages.
        
        Args:
            command_value: The value part of the ARG instruction
        """
        if not command_value.strip():
            return
            
        # Parse the command value using shlex to handle quoted strings properly
        parts = shlex.split(command_value)
        if not parts:
            return
            
        # Process each part to extract name-value pairs
        i = 0
        while i < len(parts):
            part = parts[i]
            
            if '=' in part:
                # Form: name=value
                name_value = part.split('=', 1)
                name = name_value[0].strip()
                value = name_value[1].strip() if len(name_value) > 1 else ""
                self._set_global_arg_variable(name, value)
                i += 1
            else:
                # Form: name (no value)
                name = part.strip()
                self._set_global_arg_variable(name, None)
                i += 1

    def _set_global_arg_variable(self, name, value):
        """
        Set a global ARG variable, resolving variable references in the value.
        
        Args:
            name: ARG variable name
            value: ARG variable value (can be None)
        """
        if not name:
            return
            
        # Resolve variable references in the value
        if value is not None:
            value = self._resolve_variable_references(value)
            value = self._unquote_value(value)
        
        # Store global ARG variable
        self.global_args[name] = value

    def _parse_env_instruction(self, command_value):
        """
        Parse ENV instruction with support for multiple formats:
        - ENV key=value
        - ENV key value
        - ENV key1=value1 key2=value2
        - ENV key1=value1 key2 value2
        - ENV key1=value1 key2=$key1
        
        Args:
            command_value: The value part of the ENV instruction
        """
        if not command_value.strip():
            return
            
        # Parse the command value using shlex to handle quoted strings properly
        parts = shlex.split(command_value)
        if not parts:
            return
            
        # Process each part to extract key-value pairs
        i = 0
        while i < len(parts):
            part = parts[i]
            
            if '=' in part:
                # Form: key=value
                key_value = part.split('=', 1)
                key = key_value[0].strip()
                value = key_value[1].strip() if len(key_value) > 1 else ""
                self._set_env_variable(key, value)
                i += 1
            else:
                # Form: key value (value can be multiple parts)
                key = part.strip()
                value_parts = []
                i += 1
                
                # Collect all parts until we find another key=value pair or run out of parts
                while i < len(parts):
                    next_part = parts[i]
                    if '=' in next_part:
                        # Found next key=value pair, stop here
                        break
                    value_parts.append(next_part)
                    i += 1
                
                # Set the environment variable
                value = ' '.join(value_parts).strip() if value_parts else None
                self._set_env_variable(key, value)
    
    def _set_env_variable(self, key, value):
        """
        Set an environment variable, handling PATH specially and resolving variable references.
        
        Args:
            key: Environment variable name
            value: Environment variable value (can be None)
        """
        if not key:
            return
            
        # Resolve variable references in the value
        if value is not None:
            value = self._resolve_variable_references(value)
            
            # Remove surrounding quotes if present
            value = self._unquote_value(value)
        
        # Special handling for PATH
        if key == 'PATH':
            self._handle_path_variable(value)
        else:
            # Store other environment variables
            if self.current_stage:
                if 'env' not in self.stages[self.current_stage]:
                    self.stages[self.current_stage]['env'] = {}
                self.stages[self.current_stage]['env'][key] = value
    
    def _resolve_variable_references(self, value):
        """
        Resolve variable references in a string value using the unified VariableResolver.
        
        Args:
            value: String value that may contain variable references
            
        Returns:
            String with variable references resolved
        """
        if not value:
            return value
        
        # Update resolver with current state
        self._update_variable_resolver()
        return self._variable_resolver.resolve(value) # type: ignore
    
    def _unquote_value(self, value):
        """
        Remove surrounding quotes from a value if present.
        
        Args:
            value: String value that may be quoted
            
        Returns:
            Unquoted string value
        """
        if not value:
            return value
            
        # Remove surrounding quotes if present
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            if len(value) > 1:  # Avoid stripping if value is just " or '
                return value[1:-1]
        
        return value
    
    def _handle_path_variable(self, value):
        """
        Handle PATH environment variable using the PathManager.
        
        Args:
            value: New PATH value
        """
        if not value:
            return
        
        # Update PATH using PathManager
        new_path = self._path_manager.update_path(value, self._path_manager.get_current_path())
        self._path_manager.set_current_path(new_path)
        
        # Update stage PATH
        if self.current_stage:
            self.stage_paths[self.current_stage] = new_path.copy()
    
    def _parse_arg_instruction(self, command_value):
        """
        Parse ARG instruction with support for multiple formats:
        - ARG name
        - ARG name=value
        - ARG name1=value1 name2=value2
        - ARG name1=value1 name2
        
        Args:
            command_value: The value part of the ARG instruction
        """
        if not command_value.strip():
            return
            
        # Parse the command value using shlex to handle quoted strings properly
        parts = shlex.split(command_value)
        if not parts:
            return
            
        # Process each part to extract name-value pairs
        i = 0
        while i < len(parts):
            part = parts[i]
            
            if '=' in part:
                # Form: name=value
                name_value = part.split('=', 1)
                name = name_value[0].strip()
                value = name_value[1].strip() if len(name_value) > 1 else ""
                self._set_arg_variable(name, value)
                i += 1
            else:
                # Form: name (no value)
                name = part.strip()
                self._set_arg_variable(name, None)
                i += 1
    
    def _set_arg_variable(self, name, value):
        """
        Set an ARG variable, resolving variable references in the value.
        
        Args:
            name: ARG variable name
            value: ARG variable value (can be None)
        """
        if not name:
            return
            
        # Resolve variable references in the value
        if value is not None:
            value = self._resolve_variable_references(value)
            value = self._unquote_value(value)
        
        # Store ARG variable
        if self.current_stage:
            # Stage-specific ARG
            if self.current_stage not in self.stage_args:
                self.stage_args[self.current_stage] = {}
            self.stage_args[self.current_stage][name] = value
        else:
            # Global ARG (before first FROM)
            self.global_args[name] = value

    def _normalize_path(self, path, base_path=None):
        """
        Normalize path using the PathManager.
        """
        return self._path_manager.normalize_path(path, base_path)

    def _parse_env_parameter(self, string):
        """
        Parse environment variable parameter using the unified VariableResolver.
        This method is kept for backward compatibility but now delegates to VariableResolver.
        """
        # Update resolver with current state
        self._update_variable_resolver()
        return self._variable_resolver.resolve(string) # type: ignore

    def _resolve_glob_pattern(self, pattern, base_path):
        """
        Resolve glob pattern to list of matching files.
        Handles various glob patterns like *.ext, prefix*, **, etc.
        Returns list of (source_path, is_dir) tuples.
        """
        import glob
        
        # Handle absolute paths
        if pattern.startswith('/'):
            full_pattern = pattern
        else:
            full_pattern = Path(base_path).joinpath(pattern)
            
        # Normalize pattern
        full_pattern = Path(full_pattern)
        
        # Special handling for ** pattern
        if '**' in pattern:
            # Replace ** with recursive glob pattern
            matches = glob.glob(full_pattern.as_posix(), recursive=True)
        else:
            matches = glob.glob(full_pattern.as_posix())
            
        results = []
        for match in matches:
            # Skip .git directory
            if '.git' in match.split(os.sep):
                continue
            # Skip files that should be ignored by .dockerignore
            if self._should_ignore_file(match):
                # logger.debug(f'Ignoring glob pattern: {match} in {base_path}')
                continue
            results.append((match, Path(match).is_dir()))
            
        return results

    def analyze(self):
        """
        Analyzes the Dockerfile instruction by instruction in a single pass.
        """
        self.current_workdir = self.DEFAULT_WORKDIR  # Reset workdir for the whole analysis
        self.current_stage = None  # Reset current stage
        self._path_manager.set_current_path(self.default_path.copy())  # Default PATH
        
        self.stages = {}  # Store info about each build stage (workdir, files, base_image)
        self.stage_files = {} # Files available in each stage, will be populated as COPY/ADD happens
        self.stage_base_images = {}  # Base image for each stage
        self.stage_paths = {}  # Path for each stage
        
        # Track analysis warnings and errors
        self.warnings = []
        self.errors = []
        
        # Validate Dockerfile structure
        if not self.dfp.structure:
            self.errors.append("Dockerfile appears to be empty or invalid")
            return self._create_error_result()
        
        # Check for valid Dockerfile structure
        # ARG is allowed before FROM, but we need at least one FROM instruction
        has_from = False
        first_from_index = -1
        
        # First pass: find first FROM and check structure
        for i, instruction in enumerate(self.dfp.structure):
            instruction_type = instruction['instruction'].upper()
            if instruction_type == 'FROM':
                if first_from_index == -1:
                    first_from_index = i
                has_from = True
            elif instruction_type == 'ARG':
                # ARG before FROM is valid and will be handled in global_args
                continue
            elif instruction_type == 'COMMENT':
                # Skip comments
                continue
            elif not has_from and instruction_type not in ['ARG', 'COMMENT']:
                # Only ARG and COMMENT are allowed before FROM
                self.warnings.append(f"Instruction {instruction_type} at line {i+1} appears before FROM (only ARG is allowed)")
        
        if not has_from:
            self.errors.append("No FROM instruction found - Dockerfile must contain at least one FROM instruction")
            return self._create_error_result()
        
        # Second pass: process instructions
        for i, instruction in enumerate(self.dfp.structure):
            try:
                command_type = instruction['instruction'].upper()
                command_value = instruction['value']
                cmd_info = {
                    'type': command_type,
                    'value': f"{command_type} {command_value}",
                    'workdir': self.current_workdir,
                    'stage': self.current_stage,
                    'files': []
                }
                # Validate instruction format
                if not command_type or command_type not in self.SUPPORTED_INSTRUCTIONS:
                    self.warnings.append(f"Unsupported instruction at line {i+1}: {command_type}")
                    continue

                # Handle COMMENT instructions (skip them in analysis)
                if command_type == 'COMMENT':
                    continue

                # Handle ARG before FROM (global ARG)
                if command_type == 'ARG' and i < first_from_index:
                    self._parse_global_arg_instruction(command_value)
                    self._add_command(cmd_info)
                    continue

                if command_type == 'FROM':
                    self._parse_from_instruction(command_value)
                    self._add_command(cmd_info)
                
                elif command_type == 'WORKDIR':
                    self.current_workdir = self._normalize_path(command_value, self.current_workdir)
                    if self.current_stage:
                        self.stages[self.current_stage]['workdir'] = self.current_workdir
                    cmd_info['workdir'] = self.current_workdir
                    self._add_command(cmd_info)
                        
                elif command_type == 'ENV':
                    self._parse_env_instruction(command_value)
                    self._add_command(cmd_info)
                elif command_type == 'ARG':
                    self._parse_arg_instruction(command_value)
                    self._add_command(cmd_info)

                elif command_type == 'COPY' or command_type == 'ADD':
                    affected_files = self._analyze_copy_add(command_value, command_type)
                    cmd_info['files'] = affected_files
                    self._add_command(cmd_info)

                elif command_type in ['RUN', 'CMD', 'ENTRYPOINT']:
                    self._analyze_executable_command(command_value, command_type)
                     # This instruction calls add_command/add_file internally

                elif command_type == 'SHELL':
                    self.command_executions.append({
                        'type': command_type,
                        'value': command_value,
                        'workdir': self.current_workdir,
                        'stage': self.current_stage
                    })
                    self._add_command(cmd_info)
                # Add support for missing instructions
                elif command_type == 'USER':
                    self._parse_user_instruction(command_value)
                    self._add_command(cmd_info)
                    
                elif command_type == 'VOLUME':
                    self._parse_volume_instruction(command_value)
                    self._add_command(cmd_info)
                elif command_type == 'EXPOSE':
                    self._parse_expose_instruction(command_value)
                    self._add_command(cmd_info)
                elif command_type == 'HEALTHCHECK':
                    self._parse_healthcheck_instruction(command_value)
                    self._add_command(cmd_info)
                elif command_type == 'LABEL':
                    # Track labels for potential use in analysis
                    if self.current_stage:
                        if 'labels' not in self.stages[self.current_stage]:
                            self.stages[self.current_stage]['labels'] = {}
                        self._parse_label_instruction(command_value, self.stages[self.current_stage]['labels'])
                        self._add_command(cmd_info)
                    else:
                        self.warnings.append(f"LABEL instruction before FROM at line {i+1}")
                        
            except Exception as e:
                error_msg = f"Error parsing instruction at line {i+1}: {str(e)}"
                self.errors.append(error_msg)
                logger.error(f"Error: {error_msg} {traceback.format_exc()}")
                continue
        links = []
        for _file, info in self.all_files.items():
            if len(info) > 1:
                links.append([self.all_commands[info[i]['command_index']]['index'] for i in range(len(info))])
        links = list(set([tuple(link) for link in links]))
        full_links = []
        for link in links:
            full_link_dict = {'link_group': link, 'link_details': []}
            for index in link:
                full_link = {}
                info = self.all_commands[index]
                full_link['value'] = info['value']
                full_link['workdir'] = info['workdir']
                full_link['stage'] = info['stage']
                full_link_dict['link_details'].append(full_link)
            full_links.append(full_link_dict)
        # logger.info(f"linked commands: {full_links}")
        return {
            "file_dependencies": self.file_dependencies,
            "command_executions": self.command_executions,
            "stages": self.stages,
            "stage_paths": self.stage_paths,
            "stage_base_images": self.stage_base_images,
            "global_args": self.global_args,
            "stage_args": self.stage_args,
            "warnings": self.warnings,
            "errors": self.errors,
            "all_commands": self.all_commands,
            "all_files": self.all_files,
            "linked_commands": full_links
        }

    def _create_error_result(self):
        """
        Create result structure when analysis fails.
        
        Returns:
            Error result dictionary
        """
        return {
            "file_dependencies": [],
            "command_executions": [],
            "stages": {},
            "stage_paths": {},
            "stage_base_images": {},
            "global_args": {},
            "stage_args": {},
            "warnings": self.warnings,
            "errors": self.errors,
            "all_commands": {},
            "all_files": {}
        }

    def _analyze_copy_add(self, value, command_type):
        """
        Analyzes COPY and ADD instructions to identify file dependencies.
        Handles --from=<stage> and --chown=<user>:<group> directives.
        """
        parts = value.split()
        added_files = []
        if not parts:
            return added_files
        
        # Check for options
        from_stage = None
        chown_spec = None
        source_start_idx = 0
        
        i = 0
        while i < len(parts):
            part = parts[i]
            
            # Handle --from directive
            if part.startswith('--from='):
                from_stage = part[len('--from='):].strip()
                i += 1
                continue
            
            # Handle --chown directive
            elif part.startswith('--chown='):
                chown_spec = part[len('--chown='):].strip()
                i += 1
                continue
            
            # Handle other potential options
            elif part.startswith('--'):
                # Skip unknown options
                i += 1
                continue
            
            # This should be the first source
            else:
                source_start_idx = i
                break
        
        destination_container = parts[-1]
        sources = parts[source_start_idx:-1]

        # Normalize destination path
        destination_container = self._normalize_path(destination_container, self.current_workdir)

        # If copying from another stage
        if from_stage:
            for src in sources:
                src_path = self._normalize_path(src, self.stages.get(from_stage, {}).get('workdir', '/'))
                
                # Record dependency
                dep_info = {
                    'source': from_stage,
                    'source_path': src_path,
                    'destination': self.current_stage,
                    'destination_path': destination_container,
                    'type': command_type,
                    'status': 'copied',
                    'chown': chown_spec
                }
                
                # Check if destination is in PATH
                is_in_path, final_path = self._path_manager.is_path_in_directory(
                    destination_container, 
                    self._path_manager.get_current_path()
                )
                
                if is_in_path:
                    dep_info['in_path'] = True
                    dep_info['path_location'] = final_path
                
                added_file = dep_info.copy()
                added_file['command_index'] = self.current_command_index
                added_file['path_from_repo'] = f'{from_stage}@{src_path}'
                added_files.append(self._add_file(added_file))
                
                self.file_dependencies.append(dep_info)
                
                # Add file to current stage
                if self.current_stage:
                    self.stage_files[self.current_stage].append(destination_container)
        else:
            # Normal COPY/ADD from build context
            for src_repo in sources:
                if "://" in src_repo: # crude check for URL
                    dep_info = {
                        'source': 'url',
                        'source_path': src_repo,
                        'destination': self.current_stage,
                        'destination_path': destination_container,
                        'type': command_type,
                        'status': 'copied',
                        'chown': chown_spec
                    }
                    
                    # Check if destination is in PATH
                    is_in_path, final_path = self._path_manager.is_path_in_directory(
                        destination_container, 
                        self._path_manager.get_current_path()
                    )
                    
                    if is_in_path:
                        dep_info['in_path'] = True
                        dep_info['path_location'] = final_path
                    
                    self.file_dependencies.append(dep_info)

                    added_file = dep_info.copy()
                    added_file['command_index'] = self.current_command_index
                    added_file['path_from_repo'] = f'repo@{src_repo}'
                    added_files.append(self._add_file(added_file))
                    
                    # Add file to current stage
                    if self.current_stage:
                        self.stage_files[self.current_stage].append(destination_container)
                else:
                    # Handle glob patterns
                    matches = self._resolve_glob_pattern(src_repo, self.workspace_path)
                    
                    for src_path, is_dir in matches:
                        if is_dir:
                            # Handle directory copying recursively
                            file_pairs = self._scan_directory(src_path, destination_container)
                            for repo_path, container_path in file_pairs:
                                dep_info = {
                                    'source': 'repo',
                                    'source_path': repo_path,
                                    'destination': self.current_stage,
                                    'destination_path': container_path,
                                    'type': command_type,
                                    'status': 'copied',
                                    'chown': chown_spec
                                }
                                
                                # Check if destination is in PATH
                                is_in_path, final_path = self._path_manager.is_path_in_directory(
                                    container_path, 
                                    self._path_manager.get_current_path()
                                )
                                
                                if is_in_path:
                                    dep_info['in_path'] = True
                                    dep_info['path_location'] = final_path
                                
                                self.file_dependencies.append(dep_info)

                                added_file = dep_info.copy()
                                added_file['command_index'] = self.current_command_index
                                added_file['path_from_repo'] = f'repo@{repo_path}'
                                added_files.append(self._add_file(added_file))
                                
                                # Add file to current stage
                                if self.current_stage:
                                    self.stage_files[self.current_stage].append(container_path)
                        else:
                            # For single files, determine destination path
                            if destination_container.endswith('/'):
                                # If destination ends with /, append source filename
                                dest_path = Path(destination_container).joinpath(Path(src_path).name)
                            else:
                                dest_path = destination_container
                                
                            dep_info = {
                                'source': 'repo',
                                'source_path': src_path,
                                'destination': self.current_stage,
                                'destination_path': dest_path,
                                'type': command_type,
                                'status': 'copied',
                                'chown': chown_spec
                            }
                            
                            # Check if destination is in PATH
                            is_in_path, final_path = self._path_manager.is_path_in_directory(
                                dest_path, 
                                self._path_manager.get_current_path()
                            )
                            
                            if is_in_path:
                                dep_info['in_path'] = True
                                dep_info['path_location'] = final_path
                            
                            self.file_dependencies.append(dep_info)

                            added_file = dep_info.copy()
                            added_file['command_index'] = self.current_command_index
                            added_file['path_from_repo'] = f'repo@{src_path}'
                            added_files.append(self._add_file(added_file))
                            
                            # Add file to current stage
                            if self.current_stage:
                                self.stage_files[self.current_stage].append(dest_path)
        return added_files

    def _update_file_path(self, old_path, new_path, operation_type):
        """
        Update file paths in file_dependencies and stage_files when files are moved.
        For 'mv', updates existing entries. For 'cp', new entries might be needed (handled partially).
        """
        # Update file_dependencies for 'mv'
        if operation_type == 'mv':
            for dep in self.file_dependencies:
                if dep.get('destination') == self.current_stage and dep.get('destination_path') == old_path:
                    dep['destination_path'] = new_path
                    dep['status'] = 'moved' # More specific status

        # Update stage_files for the current stage
        if self.current_stage and self.current_stage in self.stage_files:
            try:
                if old_path in self.stage_files[self.current_stage]:
                    if operation_type == 'mv':
                        self.stage_files[self.current_stage].remove(old_path)
                        if new_path not in self.stage_files[self.current_stage]:
                            self.stage_files[self.current_stage].append(new_path)
                    # For 'cp', the old path remains, new one is added
                else:
                    if operation_type == 'cp':
                        # Simplified: just add the new path. A more complex cp might involve recursion for dirs.
                        if new_path not in self.stage_files[self.current_stage]:
                            self.stage_files[self.current_stage].append(new_path)
                            # Optionally, add to file_dependencies if source was known
                            # This part requires more thought on how to represent internal copies.
                            # For now, focusing on stage_files consistency for the copy destination.

            except ValueError: # old_path might not be in the list
                pass
            except Exception as e:
                logger.error(f"Error updating stage_files: {e}")

    def _analyze_file_operation(self, command):
        """
        Analyze file operations (mv, cp) and update file paths.
        """
        # Match mv/cp commands with source and destination
        # Regex simplified to capture common cases; complex args might not be caught.
        file_op_match = re.match(self.FILE_OP_PATTERN, command)
        if file_op_match:
            op_type = file_op_match.group(1)
            # group(2) is for -r flag, not used yet but captured
            source = file_op_match.group(3)
            dest = file_op_match.group(4)
            
            # Handle relative paths
            norm_source = self._normalize_path(source, self.current_workdir) if not source.startswith('/') else self._normalize_path(source)
            norm_dest = self._normalize_path(dest, self.current_workdir) if not dest.startswith('/') else self._normalize_path(dest)
            
            # Update file paths in dependencies and stage_files
            self._update_file_path(norm_source, norm_dest, op_type)
            return True
        return False

    def _check_file_usage(self, command):
        """
        Check if any copied files are used in the command.
        Splits command into elements and checks each against copied files.
        """
        elements = []
        try:
            # Use shlex.split for more robust command splitting
            elements = shlex.split(command)
        except ValueError:
            # Fallback for commands shlex might not handle (e.g. unterminated quotes)
            # This is a simple fallback; the original manual splitting can be used if preferred for such cases.
            current = []
            in_quotes = False
            quote_char = None
            for char in command:
                if char in ['"', "'"]:
                    if not in_quotes:
                        in_quotes = True
                        quote_char = char
                    elif char == quote_char:
                        in_quotes = False
                        quote_char = None
                    current.append(char)
                elif char.isspace() and not in_quotes:
                    if current:
                        elements.append(''.join(current))
                        current = []
                else:
                    current.append(char)
            if current:
                elements.append(''.join(current))

        # Check each element against copied files
        for element in elements:
            # Skip command names and options
            if element.startswith('-') or element in ['cd', 'mkdir', 'rm', 'mv', 'cp', 'ln']:
                continue
                
            # Remove quotes if present
            element = element.strip('"\'')
            
            # Skip empty elements
            if not element:
                continue
                
            # Build and normalize full path
            if not element.startswith('/'):
                full_path = self._normalize_path(element, self.current_workdir)
            else:
                full_path = self._normalize_path(element)
                
            # Check if this path matches any copied file in the current stage
            for dep in self.file_dependencies:
                if 'destination_path' in dep and dep['destination_path'] == full_path:
                    # Only consider files in current stage
                    if 'stage' not in dep or dep['stage'] == self.current_stage:
                        dep['status'] = 'used'
                        break

    def _parse_exec_form(self, value):
        """
        Parse exec form command (JSON array format) to extract command and arguments.
        Returns a list of command elements.
        """
        try:
            # Attempt to load the value as a JSON array
            elements = json.loads(value)
            if not isinstance(elements, list):
                logger.warning(f"Warning: Exec form command did not parse to a list: {value}")
                return []
            
            # Ensure all elements are strings, as expected for command parts
            return [str(elem) for elem in elements if isinstance(elem, str | int | float)]
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing exec form command with json.loads: {value}, error: {str(e)}")
            # Fallback or attempt original parsing if specific compatibility is needed,
            # but for correctness, json.loads is preferred.
            # For now, return empty on error.
            return []
        except Exception as e: # Catch any other unexpected errors
            logger.error(f"Unexpected error in _parse_exec_form: {value}, error: {str(e)}")
            return []

    def _check_command_in_path(self, command):
        """
        Check if a command is found in the current PATH and determine its source.
        Returns (found, path, source) tuple where:
        - found: boolean indicating if command can be resolved
        - path: resolved path of the command if found, None otherwise
        - source: string indicating the source of the command:
          - 'context': command is from a file copied into the build
          - 'base': command is known to exist in base image
          - 'unknown': command path cannot be determined
        """
        # Extract first word (command)
        cmd_match = re.match(r'^\s*(\S+)', command)
        if not cmd_match:
            return False, None, 'unknown'
        
        cmd = cmd_match.group(1)
        
        # Case 1: Command is an explicit path (absolute or relative)
        if '/' in cmd:
            full_path = self._normalize_path(cmd, self.current_workdir)
            for dep in self.file_dependencies:
                if dep['destination_path'] == full_path and dep['destination'] == self.current_stage:
                    dep['status'] = 'used'
                    return True, full_path, 'context'
            return True, full_path, 'unknown'  # Path exists but source unknown
        
        # Case 2: Command is a name, check against files explicitly marked as 'in_path'
        for dep in self.file_dependencies:
            if dep.get('in_path') and Path(dep.get('path_location', '')).name == cmd:
                if dep['destination'] == self.current_stage:
                    dep['status'] = 'used'
                    return True, dep['path_location'], 'context'

        # Case 3: Command is a name, search in each directory of current_path for a copied file
        current_path = self._path_manager.get_current_path()
        for path_dir in current_path:
            potential_path = self._normalize_path(Path(path_dir).joinpath(cmd))
            for dep in self.file_dependencies:
                if dep['destination_path'] == potential_path and dep['destination'] == self.current_stage:
                    dep['status'] = 'used'
                    return True, potential_path, 'context'
        
        # Case 4: Check if command is known to exist in base image
        # This is a heuristic. It relies on Config.Cmd/Entrypoint which define default commands,
        # not necessarily an exhaustive list of all available commands on the PATH.
        # More accurate assessment requires detailed base_image_info (e.g., a list of executables).
        stage_base = self.stage_base_images.get(self.current_stage)
        if stage_base and stage_base in self.base_image_info:
            base_info = self.base_image_info[stage_base]
            # Check Config.Cmd and Config.Entrypoint for common commands
            config = base_info.get('Config', {})
            base_cmds = []
            if isinstance(config.get('Cmd'), list):
                base_cmds.extend(config['Cmd'])
            if isinstance(config.get('Entrypoint'), list):
                base_cmds.extend(config['Entrypoint'])
            
            # If command is used in base image's CMD/ENTRYPOINT, assume it exists
            if cmd in base_cmds or any(cmd in c for c in base_cmds):
                # Default to first PATH entry if actual location unknown from base_image_info
                current_path = self._path_manager.get_current_path()
                if current_path: # Ensure current_path is not empty
                    return True, Path(current_path[0]).joinpath(cmd), f'base:{stage_base}'
                else: # If no PATH, cannot determine location
                    return True, cmd, f'base:{stage_base}' # Command assumed from base, but path unknown
        
        return False, None, 'unknown'

    def _handle_cd_command(self, command):
        """
        Handle cd command and update current working directory.
        
        Args:
            command: The cd command string
            
        Returns:
            True if command was a cd command, False otherwise
        """
        cd_match = re.match(self.CD_COMMAND_PATTERN, command)
        if cd_match:
            new_dir = cd_match.group(1)
            # Handle relative and absolute paths
            if new_dir.startswith('/'):
                self.current_workdir = new_dir
            else:
                self.current_workdir = Path(self.current_workdir).joinpath(new_dir)
            return True
        return False
    
    def _process_subcommand(self, sub_cmd_content, command_type):
        """
        Process a single subcommand, handling cd, file operations, and PATH checks.
        
        Args:
            sub_cmd_content: The subcommand content
            command_type: Type of the parent command
            
        Returns:
            Command info dictionary
        """
        # Check for cd command
        if self._handle_cd_command(sub_cmd_content):
            return None  # cd command doesn't create execution record
        
        # Check for file operations
        self._analyze_file_operation(sub_cmd_content)
        
        # Check for file usage in command
        self._check_file_usage(sub_cmd_content)
        
        # Check command in PATH
        found, cmd_path, source = self._check_command_in_path(sub_cmd_content)
        
        cmd_info = {
            'type': command_type,
            'value': sub_cmd_content,
            'workdir': self.current_workdir,
            'stage': self.current_stage
        }
        
        if found:
            cmd_info['command_path'] = cmd_path
            cmd_info['source'] = source
        
        # Analyze build system dependencies
        affected_files, affected_globs = self._analyze_build_system_dependencies(sub_cmd_content)
        added_files = []
        if len(affected_files) > 0:
            for file in affected_files:
                file_info = {
                    'source': 'repo',
                    'source_path': file,
                    'destination': self.current_stage,
                    'destination_path': file,
                    'path_from_repo': f'repo@{file}',
                    'command_index': self.current_command_index
                }
                added_files.append(self._add_file(file_info))
        cmd_info['files'] = added_files
        self._add_command(cmd_info.copy())
        return cmd_info

    def _analyze_executable_command(self, value, command_type):
        """
        Analyzes RUN, CMD, ENTRYPOINT instructions.
        Splits commands by '&&' or ';' and processes each subcommand.
        For RUN instructions, also handles BuildKit features like --mount.
        """
        if command_type == 'RUN':
            # Check for BuildKit features
            buildkit_options = self._extract_buildkit_options(value)
            
            # Remove BuildKit options from command for analysis
            clean_value = self._remove_buildkit_options(value)
            split_symbol = self.COMMAND_SPLITTERS[0] if self.COMMAND_SPLITTERS[0] in clean_value else self.COMMAND_SPLITTERS[1]
            sub_commands = [cmd.strip() for cmd in clean_value.split(split_symbol) if cmd.strip()]
            sub_commands = [re.sub(r'\s+', ' ', cmd) for cmd in sub_commands]  # collapse consecutive whitespace
            
            for sub_cmd_content in sub_commands:
                # Process the subcommand
                cmd_info = self._process_subcommand(sub_cmd_content, command_type)
                if cmd_info: # Only append if it's not a cd command
                    # Add BuildKit options if present
                    if buildkit_options:
                        cmd_info['buildkit_options'] = buildkit_options
                    self.command_executions.append(cmd_info)
        else: # CMD, ENTRYPOINT
            is_exec_form = value.strip().startswith('[') and value.strip().endswith(']')

            if is_exec_form:
                # Parse exec form command
                elements = self._parse_exec_form(value)
                added_files = []
                if elements:
                    # First element is the command, rest are arguments
                    cmd = elements[0]
                    args = elements[1:]
                    
                    # Check command in PATH
                    found, cmd_path, source = self._check_command_in_path(cmd)
                    
                    # Check arguments for file paths
                    for arg in args:
                        if not arg.startswith('-') and not arg.startswith('--'):  # Skip options
                            if not arg.startswith('/'):
                                arg_path = self._normalize_path(arg, self.current_workdir)
                            else:
                                arg_path = self._normalize_path(arg)
                            file_info = {
                                'source': 'repo',
                                'source_path': arg_path,
                                'destination': self.current_stage,
                                'destination_path': arg_path,
                                'path_from_repo': f'repo@{arg_path}',
                                'command_index': self.current_command_index
                            }
                            added_files.append(self._add_file(file_info))
                            # Check if argument is a file path
                            for dep in self.file_dependencies:
                                if dep['destination_path'] == arg_path and dep['destination'] == self.current_stage:
                                    dep['status'] = 'used'
                                    break
                cmd_info = {
                    'type': command_type,
                    'value': value,
                    'workdir': self.current_workdir,
                    'form': 'exec',
                    'stage': self.current_stage,
                    'files': added_files
                }
                
                if found:
                    cmd_info['command_path'] = cmd_path
                    cmd_info['source'] = source
                
                self._add_command(cmd_info.copy())
                self.command_executions.append(cmd_info)
            else: # Shell form
                split_symbol = self.COMMAND_SPLITTERS[0] if self.COMMAND_SPLITTERS[0] in value else self.COMMAND_SPLITTERS[1]
                sub_commands = [cmd.strip() for cmd in value.split(split_symbol) if cmd.strip()]
                
                for sub_cmd_content in sub_commands:
                    # Process the subcommand
                    cmd_info = self._process_subcommand(sub_cmd_content, command_type)
                    if cmd_info: # Only append if it's not a cd command
                        # Add shell-specific fields
                        cmd_info['original_instruction'] = value
                        cmd_info['form'] = 'shell'
                        self.command_executions.append(cmd_info)

    def _extract_buildkit_options(self, value):
        """
        Extract BuildKit options from RUN instruction.
        Currently supports --mount option.
        
        Args:
            value: The RUN instruction value
            
        Returns:
            Dictionary of BuildKit options
        """
        options = {}
        
        # Extract --mount options
        mount_pattern = r'--mount=([^,\s]+(?:,[^,\s]+)*)'
        mount_matches = re.findall(mount_pattern, value)
        
        if mount_matches:
            options['mounts'] = []
            for mount_spec in mount_matches:
                mount_info = self._parse_mount_spec(mount_spec)
                if mount_info:
                    options['mounts'].append(mount_info)
        
        return options

    def _parse_mount_spec(self, mount_spec):
        """
        Parse --mount specification.
        Args:
            mount_spec: Mount specification string
        Returns:
            Dictionary with mount information
        """
        mount_info = {}
        
        # Parse key=value pairs
        pairs = mount_spec.split(',')
        for pair in pairs:
            if '=' in pair:
                key, value = pair.split('=', 1)
                mount_info[key.strip()] = value.strip()
        
        return mount_info

    def _remove_buildkit_options(self, value):
        """
        Remove BuildKit options from command value for command analysis.
        
        Args:
            value: The RUN instruction value
            
        Returns:
            Clean command value without BuildKit options
        """
        # Remove --mount options
        clean_value = re.sub(r'--mount=[^,\s]+(?:,[^,\s]+)*', '', value)
        
        # Clean up extra whitespace
        clean_value = re.sub(r'\s+', ' ', clean_value).strip()
        
        return clean_value

    def _parse_label_instruction(self, command_value, labels_dict):
        """
        Parse LABEL instruction with support for multiple formats:
        - LABEL key=value
        - LABEL key value
        - LABEL key1=value1 key2=value2
        - LABEL key1=value1 key2 value2
        
        Args:
            command_value: The value part of the LABEL instruction
            labels_dict: Dictionary to store the labels
        """
        if not command_value.strip():
            return
            
        # Parse the command value using shlex to handle quoted strings properly
        parts = shlex.split(command_value)
        if not parts:
            return
            
        # Process each part to extract key-value pairs
        i = 0
        while i < len(parts):
            part = parts[i]
            
            if '=' in part:
                # Form: key=value
                key_value = part.split('=', 1)
                key = key_value[0].strip()
                value = key_value[1].strip() if len(key_value) > 1 else ""
                labels_dict[key] = value
                i += 1
            else:
                # Form: key value (value can be multiple parts)
                key = part.strip()
                value_parts = []
                i += 1
                
                # Collect all parts until we find another key=value pair or run out of parts
                while i < len(parts):
                    next_part = parts[i]
                    if '=' in next_part:
                        # Found next key=value pair, stop here
                        break
                    value_parts.append(next_part)
                    i += 1
                
                # Set the label
                value = ' '.join(value_parts).strip() if value_parts else ""
                labels_dict[key] = value

    def _parse_user_instruction(self, command_value):
        """
        Parse USER instruction to track user context changes.
        This affects file permissions and access patterns.
        
        Args:
            command_value: The value part of the USER instruction
        """
        if not command_value.strip():
            return
            
        # Parse user specification (can be username, uid, or username:group)
        user_spec = command_value.strip()
        
        # Store user information in current stage
        if self.current_stage:
            if 'user' not in self.stages[self.current_stage]:
                self.stages[self.current_stage]['user'] = {}
            
            # Parse user:group format
            if ':' in user_spec:
                username, group = user_spec.split(':', 1)
                self.stages[self.current_stage]['user'] = {
                    'username': username.strip(),
                    'group': group.strip(),
                    'uid': None,
                    'gid': None
                }
            else:
                # Check if it's a numeric UID
                try:
                    uid = int(user_spec)
                    self.stages[self.current_stage]['user'] = {
                        'username': None,
                        'group': None,
                        'uid': uid,
                        'gid': None
                    }
                except ValueError:
                    # Assume it's a username
                    self.stages[self.current_stage]['user'] = {
                        'username': user_spec,
                        'group': None,
                        'uid': None,
                        'gid': None
                    }
            
            # Track user change in command executions for analysis
            self.command_executions.append({
                'type': 'USER',
                'value': command_value,
                'workdir': self.current_workdir,
                'stage': self.current_stage,
                'user_context': self.stages[self.current_stage]['user']
            })

    def _parse_volume_instruction(self, command_value):
        """
        Parse VOLUME instruction to track volume mount points.
        These affect file system structure and persistence.
        
        Args:
            command_value: The value part of the VOLUME instruction
        """
        if not command_value.strip():
            return
            
        # Parse volume specification (can be single path or JSON array)
        volume_spec = command_value.strip()
        
        # Check if it's JSON array format
        if volume_spec.startswith('[') and volume_spec.endswith(']'):
            try:
                import json
                volume_paths = json.loads(volume_spec)
                if isinstance(volume_paths, list):
                    volume_paths = [str(path) for path in volume_paths if path]
                else:
                    volume_paths = []
            except (json.JSONDecodeError, ValueError):
                volume_paths = []
        else:
            # Single path or space-separated paths
            volume_paths = [path.strip() for path in volume_spec.split() if path.strip()]
        
        # Normalize and store volume paths
        normalized_paths = []
        for path in volume_paths:
            if path:
                normalized_path = self._normalize_path(path, self.current_workdir)
                normalized_paths.append(normalized_path)
        
        # Store volume information in current stage
        if self.current_stage:
            if 'volumes' not in self.stages[self.current_stage]:
                self.stages[self.current_stage]['volumes'] = []
            self.stages[self.current_stage]['volumes'].extend(normalized_paths)
            
            # Track volume declaration in command executions
            self.command_executions.append({
                'type': 'VOLUME',
                'value': command_value,
                'workdir': self.current_workdir,
                'stage': self.current_stage,
                'volume_paths': normalized_paths
            })

    def _parse_expose_instruction(self, command_value):
        """
        Parse EXPOSE instruction to track network port exposures.
        These affect network connectivity and service availability.
        
        Args:
            command_value: The value part of the EXPOSE instruction
        """
        if not command_value.strip():
            return
            
        # Parse port specification (can be single port or space-separated ports)
        port_spec = command_value.strip()
        ports = []
        
        # Handle both single port and multiple ports
        for port_str in port_spec.split():
            port_str = port_str.strip()
            if port_str:
                # Parse port:protocol format (e.g., "80/tcp")
                if '/' in port_str:
                    port, protocol = port_str.split('/', 1)
                else:
                    port, protocol = port_str, 'tcp'
                
                try:
                    port_num = int(port)
                    ports.append({
                        'port': port_num,
                        'protocol': protocol.lower()
                    })
                except ValueError:
                    # Invalid port number, skip
                    continue
        
        # Store port information in current stage
        if self.current_stage:
            if 'exposed_ports' not in self.stages[self.current_stage]:
                self.stages[self.current_stage]['exposed_ports'] = []
            self.stages[self.current_stage]['exposed_ports'].extend(ports)
            
            # Track port exposure in command executions
            self.command_executions.append({
                'type': 'EXPOSE',
                'value': command_value,
                'workdir': self.current_workdir,
                'stage': self.current_stage,
                'ports': ports
            })

    def _parse_healthcheck_instruction(self, command_value):
        """
        Parse HEALTHCHECK instruction to track health check commands.
        These may involve file system access and external dependencies.
        
        Args:
            command_value: The value part of the HEALTHCHECK instruction
        """
        if not command_value.strip():
            return
            
        # Parse healthcheck specification
        healthcheck_spec = command_value.strip()
        
        # Extract options and command
        options = {}
        command = healthcheck_spec
        
        # Parse common options: --interval, --timeout, --start-period, --retries
        option_patterns = [
            (r'--interval=(\d+[smh]?)', 'interval'),
            (r'--timeout=(\d+[smh]?)', 'timeout'),
            (r'--start-period=(\d+[smh]?)', 'start_period'),
            (r'--retries=(\d+)', 'retries')
        ]
        
        for pattern, option_name in option_patterns:
            match = re.search(pattern, healthcheck_spec)
            if match:
                options[option_name] = match.group(1)
                # Remove the option from command for cleaner parsing
                command = command.replace(match.group(0), '').strip()
        
        # Check if it's NONE (disables healthcheck)
        if command.upper() == 'NONE':
            healthcheck_type = 'NONE'
            command = None
        else:
            healthcheck_type = 'CMD'
            # The remaining part is the health check command
        
        # Store healthcheck information in current stage
        if self.current_stage:
            if 'healthcheck' not in self.stages[self.current_stage]:
                self.stages[self.current_stage]['healthcheck'] = {}
            
            self.stages[self.current_stage]['healthcheck'] = {
                'type': healthcheck_type,
                'command': command,
                'options': options
            }
            
            # Track healthcheck in command executions
            self.command_executions.append({
                'type': 'HEALTHCHECK',
                'value': command_value,
                'workdir': self.current_workdir,
                'stage': self.current_stage,
                'healthcheck_info': {
                    'type': healthcheck_type,
                    'command': command,
                    'options': options
                }
            })
            
            # If there's a command, analyze it for file dependencies
            if command and healthcheck_type == 'CMD':
                self._analyze_healthcheck_command(command)

    def _analyze_healthcheck_command(self, command):
        """
        Analyze healthcheck command for file dependencies and PATH usage.
        
        Args:
            command: The healthcheck command to analyze
        """
        # Check if command is in PATH
        found, cmd_path, source = self._check_command_in_path(command)
        
        # Check for file usage in command
        self._check_file_usage(command)
        
        # Update the last healthcheck command execution with analysis results
        for cmd_exec in reversed(self.command_executions):
            if cmd_exec['type'] == 'HEALTHCHECK':
                if found:
                    cmd_exec['command_path'] = cmd_path
                    cmd_exec['source'] = source
                break

    def _analyze_build_system_dependencies(self, command: str) -> tuple[list[str], list[str]]:
        """
        Analyze build system dependencies for a command.
        
        Args:
            command: The command to analyze
        """
        try:
            # Analyze the command using build system manager
            result = self._build_system_manager.analyze_command(
                command, 
                self.workspace_path, 
                str(self.current_workdir)
            )
            
            if result:
                # Add build system analysis to command info
                if len(result.files) > 100:
                    temp = set()
                    for file in result.files:
                        if file == self.workspace_path:
                            temp.add('./')
                        elif file.startswith(self.workspace_path):
                            temp.add(Path(file).parent.relative_to(self.workspace_path).as_posix())
                    affected_files = list(temp)
                else:
                    affected_files = [Path(file).relative_to(self.workspace_path).as_posix() for file in result.files if file.startswith(self.workspace_path)]

                affected_globs = [Path(glob).relative_to(self.workspace_path).as_posix() for glob in result.globs if glob.startswith(self.workspace_path)]

                logger.debug(f"affected files: {affected_files} for '{command}' of {self.full_build_id}")

                # for dep in self.file_dependencies:
                #     if Path(dep["source_path"]).relative_to(self.workspace_path).as_posix() in affected_files:
                #         dep['status'] = 'built'
                #     elif Path(dep["source_path"]).relative_to(self.workspace_path).as_posix() in affected_globs:
                #         dep['status'] = 'built'
                #     else:
                #         dep['status'] = 'unrelated'
                
                # logger.debug(f"Build system analysis for '{command}': {result.build_system}")
                # logger.debug(f"affected files: {affected_files}")
                return affected_files, affected_globs
            else:
                # logger.warning(f"No build system analysis for '{command}'")
                return [], []
        except Exception as e:
            logger.error(f"Error analyzing build system dependencies for '{command}': {e}, {traceback.format_exc()}")
            return [], []

# Example Usage (for testing purposes, can be removed or moved)
if __name__ == '__main__':
    from config import PROJECT_ROOT
    logger.add(str(PROJECT_ROOT / "logs" / "build_channel_analyzer_test.log"))
    # dockerfile_path = PROJECT_ROOT / "results/cloned_repos/datawhores#OF-Scraper/Dockerfile"
    # dockerfile_path = PROJECT_ROOT / "results/cloned_repos/Hive-Academy#Anubis-MCP/Dockerfile"
    repos_path = PROJECT_ROOT / "results" / "cloned_repos"
    i = 0
    for repo in Path(repos_path).iterdir():
        i += 1
        if i > 3:
            break
        dockerfile_path = repo.joinpath("Dockerfile")
        if not Path(dockerfile_path).exists():
            continue
        workspace_dir = Path(dockerfile_path).parent
    
        # Load base image info if available
        base_image_info = {}
        # try:
        #     with open(PROJECT_ROOT / "playground/dae-docker-inspect.json") as f:
        #         dae_info = json.load(f)
        #         if dae_info and len(dae_info) > 0:
        #             base_image_info["alpine"] = dae_info[0]
        # except:
        #     pass
        
        analyzer = BuildChannelAnalyzer(
            dockerfile_path=dockerfile_path, 
            workspace_path=workspace_dir,
            base_image_info=base_image_info
        )
        analysis_result = analyzer.analyze()
        logger.info(analysis_result['linked_commands'])

        # print("---- File Dependencies ----")
        # for dep in analysis_result['file_dependencies']:
        #     # if dep['status'] == 'used' or dep['status'] == 'built':
        #     logger.info(f'@{dep["source"]}:{dep["source_path"].replace(workspace_dir, ".")} | @{dep["destination"]}:{dep["destination_path"]}')

        # print("\n---- Command Executions ----")
        # for cmd_exec in analysis_result['command_executions']:
        #     logger.info(cmd_exec)
        
        # logger.info("---- All Commands ----")
        # for cmd, info in analysis_result['all_commands'].items():
        #     logger.info(f"{cmd}: {info}")
        # logger.info("---- All Files ----")
        # links = []
        # for file, info in analysis_result['all_files'].items():
        #     if len(info) > 1:
        #         # logger.info(f"{file}, {info}")
        #         links.append([analysis_result['all_commands'][info[i]['command_index']]['index'] for i in range(len(info))])
        # links = list(set([tuple(link) for link in links]))
        # logger.info(f"linked commands: {links}")

