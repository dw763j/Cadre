# Build System Detection

This module provides a simplified architecture for detecting build systems in Dockerfile RUN commands and identifying files they use from the workspace.

## Architecture

### Core Components

- **`BuildSystemDetector`** (ABC): Simple interface for build system detectors
- **`BuildContext`**: Minimal context with workspace path and container working directory
- **`BuildResult`**: Simple result containing files, globs, and metadata
- **`BuildSystemRegistry`**: Manages detector registration and command analysis
- **`BuildSystemManager`**: High-level interface for the analyzer integration

### Supported Build Systems

- **Go**: Detects `go build`, `go test`, `go install`, etc.
- **Node.js**: Detects `npm`, `yarn`, `pnpm` commands

### How It Works

1. **Command Analysis**: Given a command string, find the first matching detector
2. **File Discovery**: Detector analyzes the command and returns workspace file paths/globs
3. **Integration**: Results are added to command info in the analyzer

### Key Features

- **Simple Interface**: Just `matches(command)` and `analyze(command, ctx)`
- **Workspace-First**: All file operations happen on the local workspace
- **Fallback Support**: Go detector tries `go list -json -deps` first, falls back to patterns
- **No Complex Mapping**: Direct workspace paths, no container-to-repo translation

## Usage

```python
from dofix.build_systems.manager import BuildSystemManager

manager = BuildSystemManager()
result = manager.analyze_command("go build", "/path/to/workspace", "/app")

if result:
    print(f"Build system: {result.build_system}")
    print(f"Files: {result.files}")
    print(f"Globs: {result.globs}")
```

## Adding New Build Systems

1. Implement `BuildSystemDetector` interface
2. Register in `BuildSystemManager._register_default_detectors()`
3. Return `BuildResult` with workspace file paths

## Integration

The `BuildChannelAnalyzer` calls `_analyze_build_system_dependencies()` for each subcommand, which uses the build system manager to detect dependencies and adds them to the command info.
