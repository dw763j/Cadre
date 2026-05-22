"""
Build system detection package.

This package provides detection and analysis capabilities for various build systems
to identify implicit file dependencies in Dockerfile RUN commands.
"""

from .base import BuildSystemDetector, BuildContext, BuildResult
from .registry import BuildSystemRegistry
from .manager import BuildSystemManager
from .go import GoBuildSystemDetector
from .node import NodeBuildSystemDetector
from .python import PythonBuildSystemDetector
from .java import JavaBuildSystemDetector
from .rust import RustBuildSystemDetector
from .csharp import CSharpBuildSystemDetector
from .php import PHPBuildSystemDetector
from .ruby import RubyBuildSystemDetector
from .typescript import TypeScriptBuildSystemDetector
from .node_improved import ImprovedNodeBuildSystemDetector
from .php_improved import ImprovedPHPBuildSystemDetector
from .rust_improved import ImprovedRustBuildSystemDetector
from .make import MakeBuildSystemDetector
from .cmake import CMakeBuildSystemDetector

__all__ = [
    'BuildSystemDetector',
    'BuildContext', 
    'BuildResult',
    'BuildSystemRegistry',
    'BuildSystemManager',
    'GoBuildSystemDetector',
    'NodeBuildSystemDetector',
    'PythonBuildSystemDetector',
    'JavaBuildSystemDetector',
    'RustBuildSystemDetector',
    'CSharpBuildSystemDetector',
    'PHPBuildSystemDetector',
    'RubyBuildSystemDetector',
    'TypeScriptBuildSystemDetector',
    'ImprovedNodeBuildSystemDetector',
    'ImprovedPHPBuildSystemDetector',
    'ImprovedRustBuildSystemDetector',
    'MakeBuildSystemDetector',
    'CMakeBuildSystemDetector'
]
