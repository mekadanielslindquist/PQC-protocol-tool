"""Shared paths and docker-compose topology parsing.

Nothing here talks to the network directly - it just answers "what does
docker-compose.yml say should exist", so the rest of the API (and the
frontend) has a single, always-up-to-date source of truth instead of a
second hand-maintained list of services/ports.
"""
from __future__ import annotations

import functools
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

# management_api/ lives at the repo root, one level below the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCKER_COMPOSE_PATH = PROJECT_ROOT / "docker-compose.yml"
KEYS_DIR = PROJECT_ROOT / "keys"
CRYPTO_CONFIG_DIR = PROJECT_ROOT / "crypto-config"

ORGANIZATIONS = ["Hospital_A", "Hospital_B"]

# Services that don't publish a host port in docker-compose.yml, and the
# environment variables (with their compose-file defaults) that tell us
# where to reach them from inside the docker network anyway. These are
# best-effort: outside the docker network the hostnames below won't
# resolve, and the API will correctly report that service as unreachable
# rather than guessing.
INTERNAL_SERVICE_HINTS: dict[str, tuple[str, int]] = {
    "mqtt": ("mqtt", 1883),
}


@functools.lru_cache(maxsize=1)
def docker_binary() -> str:
    """Resolve an absolute path to the `docker` CLI.

    subprocess.run(["docker", ...]) only works if "docker" is on *this
    process's* PATH - and that's often not the same PATH an interactive
    terminal has. management_api is commonly started from an IDE run
    configuration or some other non-login-shell context on macOS, which
    frequently omits Docker Desktop's/Homebrew's bin directories (they're
    normally added by ~/.zshrc or ~/.zprofile, which those launchers don't
    source). shutil.which("docker") reflects that same narrow PATH, so we
    fall back to the well-known install locations before giving up and
    returning the bare name (so callers still get a clear "not found"
    error if truly missing).
    """
    found = shutil.which("docker")
    if found:
        return found
    for candidate in (
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        str(Path.home() / ".docker" / "bin" / "docker"),
        "/Applications/Docker.app/Contents/Resources/bin/docker",
    ):
        path_obj = Path(candidate)
        if path_obj.exists():
            # docker itself shells out to helpers by bare name - notably
            # docker-credential-desktop for registry auth, even on
            # anonymous pulls, whenever ~/.docker/config.json sets
            # "credsStore": "desktop" (Docker Desktop's default). Those
            # child-process lookups use *this process's* PATH too, so
            # finding docker itself isn't enough - the helper lives right
            # next to it, and needs its directory on PATH for docker's own
            # exec.LookPath to succeed. Fix it once, here, so every caller
            # (and everything docker itself execs) benefits without each
            # subprocess.run() needing its own env= override.
            bin_dir = str(path_obj.parent)
            path_parts = os.environ.get("PATH", "").split(os.pathsep)
            if bin_dir not in path_parts:
                os.environ["PATH"] = os.pathsep.join([bin_dir, *path_parts])
            return candidate
    return "docker"


@functools.lru_cache(maxsize=1)
def load_compose() -> dict[str, Any]:
    """Parse docker-compose.yml once per process."""
    if not DOCKER_COMPOSE_PATH.exists():
        return {}
    with open(DOCKER_COMPOSE_PATH, "r") as f:
        return yaml.safe_load(f) or {}


def service_topology() -> dict[str, dict[str, Any]]:
    """Return {service_name: {published_ports: [...], host_port, container_port}}.

    published_ports entries look like "7051:7051" or "5060:5060/udp" (as
    written in docker-compose.yml). host_port/container_port are the
    first TCP mapping we can parse, used for reachability probing.
    """
    compose = load_compose()
    services = compose.get("services", {}) or {}
    topology: dict[str, dict[str, Any]] = {}

    for name, spec in services.items():
        spec = spec or {}
        raw_ports = spec.get("ports", []) or []
        host_port = None
        container_port = None

        for mapping in raw_ports:
            mapping_str = str(mapping)
            if "/udp" in mapping_str:
                continue  # can't TCP-probe a UDP port
            core = mapping_str.split("/")[0]
            parts = core.split(":")
            if len(parts) >= 2:
                try:
                    host_port = int(parts[-2])
                    container_port = int(parts[-1])
                    break
                except ValueError:
                    continue

        topology[name] = {
            "published_ports": raw_ports,
            "host_port": host_port,
            "container_port": container_port,
        }

    return topology
