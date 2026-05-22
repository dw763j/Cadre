#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configurable via environment variables (mirrors setup script defaults)
# MAX_BUILDERS: number of builders to remove named multi-platform-{i}, i in [0, MAX_BUILDERS)
# REGISTRY_NAME: container name for local registry (default: registry)
# REGISTRY_DATA_DIR: host path for registry data (default: ${SCRIPT_DIR}/results/registry)

# usage:
#   MAX_BUILDERS=4 REGISTRY_NAME=registry REGISTRY_DATA_DIR=${SCRIPT_DIR}/results/registry \
#   ./teardown_docker_builders.sh

MAX_BUILDERS=${MAX_BUILDERS:-4}
REGISTRY_NAME=${REGISTRY_NAME:-registry}
REGISTRY_DATA_DIR=${REGISTRY_DATA_DIR:-${SCRIPT_DIR}/results/registry}

echo "[info] Checking docker and buildx availability..."
if ! command -v docker >/dev/null 2>&1; then
  echo "[error] docker not found in PATH" >&2
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "[warn] docker buildx not available; skipping builders teardown."
else
  echo "[info] Deleting ${MAX_BUILDERS} docker buildx builders named 'multi-platform-{i}'..."
  for (( i=0; i<MAX_BUILDERS; i++ )); do
    name="multi-platform-${i}"
    if docker buildx inspect "${name}" >/dev/null 2>&1; then
      echo "[remove] Removing builder ${name}"
      docker buildx rm -f "${name}" >/dev/null 2>&1 || true
    else
      echo "[skip] Builder ${name} not found."
    fi
  done

  echo "[info] Builders status after removal:"
  docker buildx ls | cat || true
fi

echo "[info] Stopping and removing registry container '${REGISTRY_NAME}' if present..."
if docker ps -a --format '{{.Names}}' | grep -Fxq "${REGISTRY_NAME}"; then
  # Stop and remove in one go
  if docker rm -f "${REGISTRY_NAME}" >/dev/null 2>&1; then
    echo "[done] Removed registry container '${REGISTRY_NAME}'."
  else
    echo "[warn] Failed to remove registry container '${REGISTRY_NAME}'."
  fi
else
  echo "[skip] Registry container '${REGISTRY_NAME}' not found."
fi

echo "[info] Deleting registry data directory: ${REGISTRY_DATA_DIR}"
if [ -n "${REGISTRY_DATA_DIR}" ] && [ -d "${REGISTRY_DATA_DIR}" ]; then
  rm -rf "${REGISTRY_DATA_DIR}"
  echo "[done] Deleted directory '${REGISTRY_DATA_DIR}'."
else
  echo "[skip] Directory '${REGISTRY_DATA_DIR}' does not exist."
fi

echo "[done] Teardown complete."


