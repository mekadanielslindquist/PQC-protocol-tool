"""Best-effort health checks for the docker-compose stack.

Two independent signals are combined, and either one being unavailable is
a normal, expected condition (e.g. this API running outside of Docker, or
docker-compose not started yet) - never an error:

1. `docker compose ps` - authoritative container state, when the Docker
   CLI/daemon is reachable from wherever this API happens to be running.
2. A raw TCP connect to each service's published host port - works even
   without Docker CLI access, as long as the port is actually published.
"""
from __future__ import annotations

import json
import socket
import subprocess

from management_api.config import PROJECT_ROOT, service_topology

DOCKER_TIMEOUT_SECONDS = 8
TCP_PROBE_TIMEOUT_SECONDS = 1.5


def docker_compose_container_states() -> dict[str, str] | None:
    """Return {service_name: state} via `docker compose ps`, or None if
    Docker isn't usable from here (not installed, daemon down, no
    permission, etc). None means "no signal", not "everything is down".
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    states: dict[str, str] = {}
    try:
        # `docker compose ps` emits either one JSON array, or one JSON
        # object per line depending on version - handle both.
        stdout = result.stdout.strip()
        if stdout.startswith("["):
            rows = json.loads(stdout)
        else:
            rows = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        for row in rows:
            service = row.get("Service") or row.get("Name")
            state = row.get("State") or row.get("Status")
            if service:
                states[service] = state
    except (json.JSONDecodeError, AttributeError):
        return None

    return states


def tcp_probe(host: str, port: int, timeout: float = TCP_PROBE_TIMEOUT_SECONDS) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def all_service_statuses() -> list[dict]:
    """Combine both signals into one status per service defined in
    docker-compose.yml.
    """
    topology = service_topology()
    docker_states = docker_compose_container_states()

    statuses = []
    for name, info in sorted(topology.items()):
        docker_state = docker_states.get(name) if docker_states else None
        reachable = None
        if info["host_port"]:
            reachable = tcp_probe("localhost", info["host_port"])

        if docker_state is not None:
            running = "running" in docker_state.lower() or "up" in docker_state.lower()
            status = "up" if running else "down"
        elif reachable is not None:
            status = "up" if reachable else "down"
        else:
            status = "unknown"

        statuses.append({
            "service": name,
            "status": status,
            "docker_state": docker_state,
            "port_reachable": reachable,
            "host_port": info["host_port"],
            "published_ports": info["published_ports"],
        })

    return statuses
