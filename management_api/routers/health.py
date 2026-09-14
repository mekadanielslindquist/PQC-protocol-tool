from fastapi import APIRouter

from management_api import crypto_bridge
from management_api.docker_status import docker_compose_container_states

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Liveness check for the management API itself (not the stack it manages)."""
    return {"status": "ok"}


@router.get("/crypto/status")
def crypto_status():
    """Whether the compiled Falcon/Kyber native libraries loaded successfully.

    False here is expected on most machines until compile_falcon_library.sh
    (and the equivalent Kyber build) has been run for the local CPU
    architecture - it's not a bug in the API.
    """
    status = crypto_bridge.get_status()
    return {"available": status.available, "error": status.error}


@router.get("/docker/status")
def docker_status():
    """Whether `docker compose ps` is usable from wherever this API is running."""
    states = docker_compose_container_states()
    return {
        "docker_cli_available": states is not None,
        "container_states": states or {},
    }
