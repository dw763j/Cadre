#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# MAX_BUILDERS=4 REGISTRY_PORT=5001 REGISTRY_NAME=registry REGISTRY_DATA_DIR=${SCRIPT_DIR}/results/registry ./setup_docker_builders.sh

# Configurable via environment variables
# MAX_BUILDERS: number of builders to ensure exist (default: 4)
# REGISTRY_NAME: container name for local registry (default: registry)
# REGISTRY_PORT: host port mapping to registry's 5000 (default: 5001)
# REGISTRY_DATA_DIR: host path for registry data (default: ${SCRIPT_DIR}/results/registry)

MAX_BUILDERS=${MAX_BUILDERS:-4}
REGISTRY_NAME=${REGISTRY_NAME:-registry}
REGISTRY_PORT=${REGISTRY_PORT:-5001}
REGISTRY_DATA_DIR=${REGISTRY_DATA_DIR:-${SCRIPT_DIR}/results/registry}

echo "[info] Ensuring docker buildx is available..."
if ! command -v docker >/dev/null 2>&1; then
  echo "[error] docker not found in PATH" >&2
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "[error] docker buildx not available. Please install/enable Buildx (Docker 19.03+)." >&2
  exit 1
fi

echo "[info] Creating and bootstrapping ${MAX_BUILDERS} builders (if missing)..."

for (( i=0; i<MAX_BUILDERS; i++ )); do
  name="multi-platform-${i}"
  if docker buildx inspect "${name}" >/dev/null 2>&1; then
    echo "[skip] Builder ${name} already exists"
  else
    echo "[create] Creating builder ${name} (driver=docker-container, network=host)"
    docker buildx create \
      --name "${name}" \
      --driver docker-container \
      --driver-opt "network=host" \
      --config config/buildkitd.toml
  fi

  echo "[bootstrap] Bootstrapping ${name}"
  docker buildx inspect --bootstrap "${name}" >/dev/null 2>&1 || true
done

echo "[info] Builders status:"
docker buildx ls | cat

echo "[info] Ensuring local registry '${REGISTRY_NAME}' is running on port ${REGISTRY_PORT}..."
mkdir -p "${REGISTRY_DATA_DIR}"

if docker ps -a --format '{{.Names}}' | grep -Fxq "${REGISTRY_NAME}"; then
  # Container exists
  if [ "$(docker inspect -f '{{.State.Running}}' "${REGISTRY_NAME}")" = "true" ]; then
    echo "[skip] Registry '${REGISTRY_NAME}' already running"
  else
    echo "[start] Starting existing registry '${REGISTRY_NAME}'"
    docker start "${REGISTRY_NAME}" >/dev/null
  fi
else
  echo "[run] Launching new registry '${REGISTRY_NAME}' (data dir: ${REGISTRY_DATA_DIR})"
  docker run \
    --detach \
    --name "${REGISTRY_NAME}" \
    -p "${REGISTRY_PORT}:5000" \
    -e REGISTRY_STORAGE_DELETE_ENABLED=true \
    --volume "${REGISTRY_DATA_DIR}:/var/lib/registry/docker/registry" \
    --restart unless-stopped \
    registry:latest >/dev/null
fi

echo "[done] Setup complete."


