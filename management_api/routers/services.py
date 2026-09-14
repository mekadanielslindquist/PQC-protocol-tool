from fastapi import APIRouter

from management_api.config import service_topology
from management_api.docker_status import all_service_statuses

router = APIRouter(prefix="/services", tags=["services"])


@router.get("")
def list_services():
    """Status of every service defined in docker-compose.yml.

    Combines `docker compose ps` (when Docker is reachable from here) with
    a raw TCP probe of each service's published host port. A service
    reporting "unknown" just means neither signal was available - for
    example this API running outside Docker against a service with no
    published port (quantum_srtp, quantum_mqtt, hedera-bridge, cli).
    """
    return {"services": all_service_statuses()}


@router.get("/topology")
def topology():
    """Raw service/port topology parsed from docker-compose.yml, for the
    frontend to render without re-implementing the compose-file parsing.
    """
    return {"services": service_topology()}
