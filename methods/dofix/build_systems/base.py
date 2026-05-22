"""
Base classes for build system detection.

This module defines the core interfaces for detecting build systems
and identifying files they use from the workspace.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class BuildContext:
    """Simple context for build system analysis."""
    workspace_path: str
    current_workdir: str  # Container working directory


@dataclass
class BuildResult:
    """Result of build system analysis."""
    files: list[str]  # Workspace file paths
    globs: list[str]  # Glob patterns for workspace files
    build_system: str
    command_type: str
    confidence: float = 1.0


class BuildSystemDetector(ABC):
    """Simple interface for build system detectors."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of the build system."""
        pass
    
    @abstractmethod
    def matches(self, command: str) -> bool:
        """Check if this detector can handle the command."""
        pass
    
    @abstractmethod
    def analyze(self, command: str, ctx: BuildContext) -> BuildResult:
        """Analyze command and return files used from workspace."""
        pass
