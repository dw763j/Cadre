import os
import json
import docker
import shutil
import subprocess

from loguru import logger
from build_dockerfile.lock_utils import acquire_write_lock, release_write_lock

def setup_docker_daemon_config(max_cache_size="30GB"):
    """Configure Docker daemon to limit BuildKit cache size."""
    try:
        config_path = "/etc/docker/daemon.json"
        config = {}
        
        # Read existing config if present
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)

        config["features"] = config.get("features", {})
        config["features"]["buildkit"] = True

        config['builder'] = config.get('builder', {})
        config['builder']['gc'] = config['builder'].get('gc', {})

        # Set BuildKit cache size limit and GC policy
        config['builder']['gc']['defaultKeepStorage'] = max_cache_size
        config['builder']['gc']['enabled'] = True
        config['builder']['gc']['policy'] = [
            {"keepStorage": "5GB", "filter": ["unused-for=24h"]},
            {"keepStorage": "30GB", "all": True}
        ]
        
        # Write config
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=4)
        
        # Restart Docker service to apply config
        subprocess.run(["systemctl", "restart", "docker"], check=True)
        logger.info(f"Docker daemon configured with cache limit: {max_cache_size}")
        return True
    except Exception as e:
        logger.error(f"Failed to configure Docker daemon: {e}")
        return False


def clean_docker_system(api_client=None, min_space_gb=50, locks=None):
    try:
        if locks:
            logger.debug("Wait for the Docker clean lock...")
            acquire_write_lock(locks)
            logger.debug("Gained the Docker clean lock, start cleaning...")

        free_gb = shutil.disk_usage("/data")[2] / (1024 ** 3)
        logger.info(f"Available disk space in /data: {free_gb:.2f} GB")

        if not api_client:
            api_client = docker.APIClient(base_url='unix://var/run/docker.sock')

        api_client.prune_containers()
        api_client.prune_builds()  # does not prune docker-container driver cache
        api_client.prune_images()
        api_client.prune_networks()
        api_client.prune_volumes()

        free_gb = shutil.disk_usage("/data")[2] / (1024 ** 3)
        logger.debug(f"Docker system gentlely pruned, now available: {free_gb:.2f} GB.")
        
        if free_gb < min_space_gb:
            try:
                subprocess.run(["docker", "system", "prune", "-a", "-f", "--volumes"], check=True)  # also misses docker-container driver cache
            except subprocess.CalledProcessError as e:
                logger.warning(f"Error running docker system prune: {e}")
            logger.debug(f"Docker system aggressive prune completed, now available: {shutil.disk_usage('/data')[2]/(1024 ** 3):.2f} GB.")

            try:
                subprocess.run(["systemctl", "restart", "docker"], check=True)
                logger.debug("Docker service restarted")
            except Exception as e:
                logger.warning(f"Docker system restart failed: {e}")

    except Exception as e:
        logger.error(f"Error during Docker system cleanup: {e}")
    finally:
        if locks:
            release_write_lock(locks)
            logger.debug("Released the Docker clean lock.")


def prune_builder_cache(builder_name: str) -> bool:
    """Prune cache for a specific buildx builder (docker-container driver).

    This runs: `docker buildx --builder <builder_name> prune -f`.

    Returns True on success, False otherwise.
    """
    try:
        if not builder_name:
            logger.warning("No builder name provided for prune; skipping.")
            return False
        cmd = [
            "docker", "buildx", "--builder", builder_name, "prune", "-af"
        ]
        subprocess.run(cmd, check=True)
        logger.info(f"Pruned buildx cache for builder: {builder_name}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to prune buildx cache for {builder_name}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error pruning builder {builder_name}: {e}")
        return False


def discover_multi_platform_builders(prefix: str = "multi-platform-") -> list[str]:
    """List buildx builder instances whose names start with ``prefix`` from ``docker buildx ls``.

    Reads each builder's top-level ``Name`` via ``--format '{{json .}}'`` (excluding sub-nodes),
    deduplicates, and sorts by numeric suffix. Returns an empty list on failure or if none match.
    """
    try:
        result = subprocess.run(
            ["docker", "buildx", "ls", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Failed to run docker buildx ls: {e}")
        return []

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        logger.warning(f"docker buildx ls failed (exit {result.returncode}): {err}")
        return []

    seen: set[str] = set()
    names: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = obj.get("Name")
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        if name not in seen:
            seen.add(name)
            names.append(name)

    def sort_key(n: str) -> tuple[int, int | str]:
        tail = n[len(prefix) :] if n.startswith(prefix) else n
        if tail.isdigit():
            return (0, int(tail))
        return (1, tail)

    names.sort(key=sort_key)
    return names


if __name__ == "__main__":
    setup_docker_daemon_config()