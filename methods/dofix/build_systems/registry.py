"""
Simple registry for build system detectors.
"""

from loguru import logger

from .base import BuildSystemDetector, BuildContext, BuildResult


class BuildSystemRegistry:
    """Simple registry for build system detectors."""
    
    def __init__(self):
        """Initialize an empty registry."""
        self._detectors: list[BuildSystemDetector] = []
    
    def register(self, detector: BuildSystemDetector) -> None:
        """Register a build system detector."""
        if detector is None:
            raise ValueError("detector cannot be None")
        
        if not isinstance(detector, BuildSystemDetector):
            raise ValueError("detector must implement BuildSystemDetector")
        
        self._detectors.append(detector)
        # logger.debug(f"Registered build system detector: {detector.name}")
    
    def analyze_command(self, command: str, ctx: BuildContext) -> BuildResult | None:
        """Analyze a build command using the first matching detector."""
        if not command or not command.strip():
            return None
        
        if ctx is None:
            return None
        
        # Find first matching detector
        for detector in self._detectors:
            try:
                if detector.matches(command):
                    # logger.debug(f"Found matching detector '{detector.name}' for command: {command[:50]}")
                    result = detector.analyze(command, ctx)
                    return result
            except Exception as e:
                logger.warning(f"Error with detector '{detector.name}': {e}")
                continue
        
        # logger.debug(f"No matching detector found for command: {command[:50]}...")
        return None
    
    def list_detectors(self) -> list[str]:
        """Get list of registered detector names."""
        return [detector.name for detector in self._detectors]
    
    def clear(self) -> None:
        """Clear all registered detectors."""
        self._detectors.clear()
        logger.debug("Cleared all build system detectors")
    
    def __len__(self) -> int:
        """Return the number of registered detectors."""
        return len(self._detectors)
