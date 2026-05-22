"""
Simple manager for build system detection.
"""

from loguru import logger

from .base import BuildContext, BuildResult
from .registry import BuildSystemRegistry
from .go import GoBuildSystemDetector
# from .node import NodeBuildSystemDetector
from .node_improved import ImprovedNodeBuildSystemDetector
from .python import PythonBuildSystemDetector
from .java import JavaBuildSystemDetector
# from .rust import RustBuildSystemDetector
from .rust_improved import ImprovedRustBuildSystemDetector
from .csharp import CSharpBuildSystemDetector
# from .php import PHPBuildSystemDetector
from .php_improved import ImprovedPHPBuildSystemDetector
from .ruby import RubyBuildSystemDetector
from .typescript import TypeScriptBuildSystemDetector
from .make import MakeBuildSystemDetector
from .cmake import CMakeBuildSystemDetector


class BuildSystemManager:
    """Simple manager for build system detection."""
    
    def __init__(self):
        """Initialize the build system manager."""
        self.registry = BuildSystemRegistry()
        self._register_default_detectors()
    
    def _register_default_detectors(self) -> None:
        """Register the default build system detectors."""
        try:
            # Register Go detector
            go_detector = GoBuildSystemDetector()
            self.registry.register(go_detector)
            # logger.debug("Registered Go build system detector")
            
            # Register Node.js detector
            # node_detector = NodeBuildSystemDetector()
            # self.registry.register(node_detector)
            # # logger.debug("Registered Node.js build system detector")
            
            # Register Improved Node.js detector
            improved_node_detector = ImprovedNodeBuildSystemDetector()
            self.registry.register(improved_node_detector)
            # logger.debug("Registered Improved Node.js build system detector")
            
            # Register Python detector
            python_detector = PythonBuildSystemDetector()
            self.registry.register(python_detector)
            # logger.debug("Registered Python build system detector")
            
            # Register Java detector
            java_detector = JavaBuildSystemDetector()
            self.registry.register(java_detector)
            # logger.debug("Registered Java build system detector")
            
            # # Register Rust detector
            # rust_detector = RustBuildSystemDetector()
            # self.registry.register(rust_detector)
            # # logger.debug("Registered Rust build system detector")
            
            # Register Improved Rust detector
            improved_rust_detector = ImprovedRustBuildSystemDetector()
            self.registry.register(improved_rust_detector)
            # logger.debug("Registered Improved Rust build system detector")
            
            # Register C# detector
            csharp_detector = CSharpBuildSystemDetector()
            self.registry.register(csharp_detector)
            # logger.debug("Registered C# build system detector")
            
            # # Register PHP detector
            # php_detector = PHPBuildSystemDetector()
            # self.registry.register(php_detector)
            # # logger.debug("Registered PHP build system detector")

            # Register Improved PHP detector
            improved_php_detector = ImprovedPHPBuildSystemDetector()
            self.registry.register(improved_php_detector)
            # logger.debug("Registered Improved PHP build system detector")
            
            # Register Ruby detector
            ruby_detector = RubyBuildSystemDetector()
            self.registry.register(ruby_detector)
            # logger.debug("Registered Ruby build system detector")
            
            # Register TypeScript detector
            typescript_detector = TypeScriptBuildSystemDetector()
            self.registry.register(typescript_detector)
            # logger.debug("Registered TypeScript build system detector")
            
            # Register Make detector
            make_detector = MakeBuildSystemDetector()
            self.registry.register(make_detector)
            # logger.debug("Registered Make build system detector")

            # Register CMake detector
            cmake_detector = CMakeBuildSystemDetector()
            self.registry.register(cmake_detector)
            # logger.debug("Registered CMake build system detector")
            
        except Exception as e:
            logger.error(f"Error registering default detectors: {e}")
    
    def analyze_command(self, command: str, workspace_path: str, current_workdir: str) -> BuildResult | None:
        """
        Analyze a build command to identify files used from workspace.
        
        Args:
            command: The command to analyze
            workspace_path: Path to the workspace
            current_workdir: Current working directory in container
            
        Returns:
            BuildResult with files and globs, or None if no detector matched
        """
        ctx = BuildContext(workspace_path=workspace_path, current_workdir=current_workdir)
        return self.registry.analyze_command(command, ctx)
