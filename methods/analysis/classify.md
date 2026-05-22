| Code Key | L1 Category | L2 Sub-category | L3 Root Cause |
|---|---|---|---|
| `APP_UV` | Application Dependency & Build | Python ecosystem | `uv`: dependency sync & operational errors |
| `APP_PIP` | Application Dependency & Build | Python ecosystem | `pip`: installation errors |
| `APP_PYPACKAGE` | Application Dependency & Build | Python ecosystem | Python package build failures (setup.py, wheel) |
| `APP_NPM` | Application Dependency & Build | JavaScript ecosystem | `npm`: build/install/CI failures |
| `APP_PNPM` | Application Dependency & Build | JavaScript ecosystem | `pnpm`: build failures |
| `APP_GO` | Application Dependency & Build | Go ecosystem | Go: build/install/dependency failures |
| `APP_OTHER` | Application Dependency & Build | Other build systems | Other tools (yarn, poetry, Gradle, Maven, Cargo…) |
| `APP_MAKE` | Application Dependency & Build | Other build systems | Makefile execution failures |
| `SYS_MISSING` | System & Environment | OS package management | Missing system dependencies (gcc, ffmpeg…) |
| `SYS_PKG` | System & Environment | OS package management | OS package manager errors (apt-get, apk add) |
| `SYS_PYVER` | System & Environment | Runtime environment | Python version incompatibility |
| `SYS_DOWNLOAD` | System & Environment | External resources | File download errors (wget, curl) |
| `DOCKER_IMAGE` | Dockerfile-specific | Image specification | Base image errors (not found, auth, invalid tag) |
| `DOCKER_COPY` | Dockerfile-specific | Context specification | File copy/add failure (COPY, ADD) |
| `SCRIPT_CUSTOM` | Script & Command | Custom execution | Developer custom script failure |
| `SCRIPT_SYNTAX` | Script & Command | Instruction syntax | RUN command syntax error |
| `INFRA_CACHE` | CI Infrastructure | Build environment | CI cache interaction error |
| `INFRA_DISK` | CI Infrastructure | Build environment | Insufficient disk space |
| `INFRA_PUSH` | CI Infrastructure | Registry & distribution | Docker image push/upload failure |
| `UNFIXABLE` | Software Internal† | Software-internal | Build failure unrelated to Dockerfile |
| `UNKNOWN` | Software Internal† | Undetermined | Cannot determine cause from log |
