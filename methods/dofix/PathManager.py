from pathlib import Path

class PathManager:
    """
    Manages path operations and PATH environment variable handling.
    Provides unified methods for path checking, normalization, and PATH updates.
    """
    
    def __init__(self, default_path):
        self.default_path = default_path.copy()
        self.current_path = default_path.copy()
    
    def normalize_path(self, path, base_path=None):
        """
        Normalize path by removing . and .. components.
        If base_path is provided, resolve relative to it.
        """
        # Remove any leading/trailing whitespace
        path = Path(path.strip()) if isinstance(path, str) else path
        
        # Handle empty path
        if not path:
            return path.as_posix()
        
        # If path is absolute, return it
        if path.is_absolute():
            return path.as_posix()
        
        # If path is relative and base_path is provided, join them
        if not path.is_absolute() and base_path:
            path = Path(base_path).joinpath(path)
            
        # Normalize the path
        # normalized = Path(path)
        
        if path.is_absolute():
            normalized = path
        else:
            normalized = '/' + path.as_posix()
            
        return str(normalized)
    
    def is_path_in_directory(self, file_path, path_directories):
        """
        Check if a file path is within any of the specified path directories.
        
        Args:
            file_path: File path to check
            path_directories: List of path directories
            
        Returns:
            Tuple of (is_in_path, final_path) where:
            - is_in_path: boolean indicating if file is in PATH
            - final_path: resolved path if in PATH, None otherwise
        """
        if not path_directories:
            return False, None
        
        # Check if destination is in PATH
        file_dir = Path(file_path).parent
        if file_dir in path_directories:
            return True, file_path
        elif file_path in path_directories:
            # If destination itself is a PATH directory
            return True, file_path
        
        return False, None
    
    def update_path(self, new_path_value, current_path):
        """
        Update PATH environment variable, handling $PATH and ${PATH} references.
        
        Args:
            new_path_value: New PATH value string
            current_path: Current PATH list
            
        Returns:
            Updated PATH list
        """
        if not new_path_value:
            return current_path
        
        if '$PATH' in new_path_value or '${PATH}' in new_path_value:
            # Handle PATH with $PATH or ${PATH} included
            new_path_elements = []
            
            # Substitute $PATH or ${PATH} which might be part of a :-separated list
            for path_segment in new_path_value.split(':'):
                if path_segment == '$PATH' or path_segment == '${PATH}':
                    new_path_elements.extend(current_path)
                elif '$PATH' in path_segment or '${PATH}' in path_segment:
                    # Handle cases like /new/path:$PATH or $PATH:/another/path
                    substituted_segment = path_segment.replace('$PATH', ':'.join(current_path))
                    substituted_segment = substituted_segment.replace('${PATH}', ':'.join(current_path))
                    new_path_elements.extend(s.strip() for s in substituted_segment.split(':') if s.strip())
                else:
                    new_path_elements.append(path_segment.strip())
            
            return [p for p in new_path_elements if p]  # Clean empty strings
        else:
            # Complete replacement of PATH
            return [p.strip() for p in new_path_value.split(':') if p.strip()]
    
    def set_current_path(self, new_path):
        """Set the current PATH"""
        self.current_path = new_path.copy()
    
    def get_current_path(self):
        """Get the current PATH"""
        return self.current_path.copy()