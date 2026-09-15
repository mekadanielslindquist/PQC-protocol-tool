"""End-to-end "does this actually work" checks, meant to be driven from the
test dashboard (management_ui/). Every check here is best-effort: it's
designed to degrade to a clear, structured explanation rather than hang or
crash when the underlying docker-compose stack isn't up, since that's the
normal state until `docker-compose up -d` has been run.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from management_api.config import PROJECT_ROOT
from management_api.docker_status import tcp_probe

router = APIRouter(prefix="/test", tags=["tests"])

DOCKER_EXEC_TIMEOUT_SECONDS = 20
MQTT_CONNECT_TIMEOUT_SECONDS = 5


# --------------------------------------------------------------------------
# MQTT
# --------------------------------------------------------------------------

class MqttTestRequest(BaseModel):
    org_id: str = "Hospital_A"
    topic: str = "quantum/test"
    message: dict = {"ping": True}
    broker_host: Optional[str] = None
    broker_port: Optional[int] = None


@router.post("/mqtt")
def test_mqtt(request: MqttTestRequest):
    """Publish a test message through the MQTT broker.

    First does a plain TCP reachability check (fast, always safe). If the
    broker is reachable, attempts an unencrypted paho-mqtt publish as a
    basic connectivity smoke test - this deliberately does NOT go through
    the quantum encryption layer (QuantumMQTTClient), because that layer
    additionally requires org keys to exist at a container-internal path
    (/app/keys/<org>.example.com/...) and a working crypto backend; use
    POST /keys/generate plus GET /crypto/status to check those separately.
    """
    host = request.broker_host or os.environ.get("MQTT_BROKER_HOST", "localhost")
    port = request.broker_port or int(os.environ.get("MQTT_BROKER_PORT", 1883))

    reachable = tcp_probe(host, port, timeout=MQTT_CONNECT_TIMEOUT_SECONDS)
    if not reachable:
        return {
            "reachable": False,
            "published": False,
            "detail": f"Could not open a TCP connection to MQTT broker at {host}:{port}. "
                      "Is the 'mqtt' service running (docker-compose up -d mqtt)?",
        }

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return {
            "reachable": True,
            "published": False,
            "detail": "paho-mqtt is not installed in this API's environment "
                      "(pip install -r requirements.txt).",
        }

    result: dict[str, Any] = {"reachable": True, "published": False, "detail": None}
    done = threading.Event()

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            client.publish(request.topic, json.dumps(request.message))
            result["published"] = True
        else:
            result["detail"] = f"Broker rejected connection (rc={rc})"
        done.set()

    client = mqtt.Client()
    client.on_connect = on_connect
    try:
        client.connect_async(host, port, keepalive=10)
        client.loop_start()
        done.wait(timeout=MQTT_CONNECT_TIMEOUT_SECONDS)
    finally:
        client.loop_stop()
        client.disconnect()

    if not done.is_set():
        result["detail"] = "Connected at the TCP level but MQTT handshake did not complete in time."

    return result


# --------------------------------------------------------------------------
# Hyperledger Fabric chaincode (via the project's own `cli` container, same
# pattern fabric_commands.sh and the README's "Integration Testing" section
# already use)
# --------------------------------------------------------------------------

class BlockchainQueryRequest(BaseModel):
    channel: str = "mychannel"
    chaincode: str = "quantum_records"
    function: str = "queryAllRecords"
    args: list[str] = []


def _docker_exec(*args: str) -> dict:
    try:
        result = subprocess.run(
            ["docker", "exec", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=DOCKER_EXEC_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return {"ok": False, "detail": "Docker CLI not found on this machine."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": f"Command timed out after {DOCKER_EXEC_TIMEOUT_SECONDS}s."}

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


@router.post("/blockchain/query")
def test_blockchain_query(request: BlockchainQueryRequest):
    """Run a chaincode query through the `cli` container, e.g.
    `docker exec cli peer chaincode query -C mychannel -n quantum_records
    -c '{"Args":["queryAllRecords"]}'`
    """
    payload = json.dumps({"function": request.function, "Args": request.args})
    return _docker_exec(
        "cli", "peer", "chaincode", "query",
        "-C", request.channel,
        "-n", request.chaincode,
        "-c", payload,
    )


# --------------------------------------------------------------------------
# Hedera bridge
# --------------------------------------------------------------------------

class HederaHealthRequest(BaseModel):
    org_id: str = "Hospital_A"


@router.post("/hedera/health")
def test_hedera_health(request: HederaHealthRequest):
    """Check the Hedera<->Fabric bridge's own health report, via the
    hedera-bridge container - mirrors the exact snippet in the README's
    "Integration Testing" section.
    """
    script = (
        "from hedera_bridge import HederaFabricBridge; "
        f"bridge = HederaFabricBridge('{request.org_id}'); "
        "import json; print(json.dumps(bridge.check_health()))"
    )
    result = _docker_exec("hedera-bridge", "python3", "-c", script)
    if result.get("ok") and result.get("stdout"):
        try:
            result["health"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return result


# --------------------------------------------------------------------------
# Peer-to-peer: does Fabric's gossip/replication protocol actually work
# between Hospital_A and Hospital_B, not just "are both containers up"
# --------------------------------------------------------------------------

class PeerCompareRequest(BaseModel):
    channel: str = "mychannel"


PEER_CONTAINERS = {
    "Hospital_A": "peer0.Hospital_A.example.com",
    "Hospital_B": "peer0.Hospital_B.example.com",
}


@router.post("/peers/compare")
def test_peer_comparison(request: PeerCompareRequest):
    """Ask each hospital's peer directly (not through `cli`) for its own
    view of the channel ledger, and compare them.

    This is a real test of the replication protocol between the two
    peers: if both report the same block height for the same channel,
    they've actually converged on the same ledger via Fabric's gossip
    protocol - not just "both containers happen to be running".
    """
    results: dict[str, Any] = {}
    for org, container in PEER_CONTAINERS.items():
        r = _docker_exec(container, "peer", "channel", "getinfo", "-c", request.channel)
        parsed = None
        if r.get("ok") and r.get("stdout"):
            # `peer channel getinfo` prints log lines followed by a JSON blob.
            for line in r["stdout"].splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        pass
                    break
        results[org] = {**r, "info": parsed}

    heights = {org: r["info"]["height"] for org, r in results.items() if r.get("info") and "height" in r["info"]}
    in_sync = len(heights) == len(PEER_CONTAINERS) and len(set(heights.values())) == 1

    return {
        "channel": request.channel,
        "peers": results,
        "heights": heights,
        "in_sync": in_sync,
    }
