import re

class VariableResolver:
    """
    Unified variable reference resolver for ARG, ENV, and other variable references.
    Supports both $VAR and ${VAR} formats with proper precedence order.
    """
    
    def __init__(self, global_args, stage_args, env_from_file, stages, current_stage):
        self.global_args = global_args
        self.stage_args = stage_args
        self.env_from_file = env_from_file
        self.stages = stages
        self.current_stage = current_stage
        
        # Pattern to match both ${VAR} and $VAR formats
        self.var_pattern = r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}|\$([a-zA-Z_][a-zA-Z0-9_]*)'
    
    def resolve(self, value):
        """
        Resolve all variable references in a string value.
        
        Args:
            value: String value that may contain variable references
            
        Returns:
            String with variable references resolved
        """
        if not value:
            return value
        
        def replace_var(match):
            var_name = match.group(1) or match.group(2)
            return self._get_variable_value(var_name) or match.group(0)
        
        return re.sub(self.var_pattern, replace_var, value)
    
    def _get_variable_value(self, var_name):
        """
        Get variable value following Docker precedence order:
        1. Global ARG variables (before first FROM)
        2. Stage-specific ARG variables
        3. .env file variables
        4. Current stage environment variables
        
        Args:
            var_name: Variable name to resolve
            
        Returns:
            Variable value if found, None otherwise
        """
        # Check global ARG variables first (highest priority)
        if var_name in self.global_args:
            return str(self.global_args[var_name])
        
        # Check stage-specific ARG variables
        if self.current_stage and self.current_stage in self.stage_args:
            if var_name in self.stage_args[self.current_stage]:
                return str(self.stage_args[self.current_stage][var_name])
        
        # Check .env file
        if var_name in self.env_from_file:
            return str(self.env_from_file[var_name])
        
        # Check current stage environment variables
        if self.current_stage and 'env' in self.stages[self.current_stage]:
            if var_name in self.stages[self.current_stage]['env']:
                return str(self.stages[self.current_stage]['env'][var_name])
        
        return None
