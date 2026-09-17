"""Hyperledger Fabric channel lifecycle: turns the manual steps in
fabric_commands.sh into a single parameterized HTTP call, so the channel
name isn't hardcoded to "mychannel" and the whole flow can be driven from
the dashboard instead of hand-typed shell commands.

Important: this orderer is configured for the channel participation API
(see ORDERER_GENERAL_BOOTSTRAPMETHOD=none / ORDERER_CHANNELPARTICIPATION_ENABLED=true
in docker-compose.yml) rather than the older system-channel/genesis-block
bootstrap model that fabric_commands.sh's generate_genesis()/create_channel()
assumed. That means channel creation here goes through `osnadmin channel
join` against a channel genesis block, not `peer channel create` against a
system channel - the old script's approach would fail against this
orderer's actual configuration.

Every Fabric CLI command runs inside the `cli` container (built from
hyperledger/fabric-tools, so `peer`/`configtxgen`/`osnadmin` are already on
its PATH), the same way the rest of this API already shells out via Docker.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from management_api.config import PROJECT_ROOT, docker_binary

router = APIRouter(prefix="/fabric", tags=["fabric"])

DOCKER_EXEC_TIMEOUT_SECONDS = 45

ORG_MSP_IDS = {"Hospital_A": "HospitalAMSP", "Hospital_B": "HospitalBMSP"}

# configtxgen's -asOrg flag wants the org's "Name:" field from configtx.yaml
# (the channel config group key), NOT its MSP ID - passing the MSP ID there
# fails with "org with name 'HospitalAMSP' does not exist in config". Two
# different identifiers for the same org; keep both maps rather than derive
# one from the other.
ORG_NAMES = {"Hospital_A": "HospitalA", "Hospital_B": "HospitalB"}

CLI_PEER_ROOT = "/opt/gopath/src/github.com/hyperledger/fabric/peer"
ORDERER_TLS_CA = f"{CLI_PEER_ROOT}/crypto/ordererOrganizations/example.com/orderers/orderer.example.com/tls/ca.crt"
ORDERER_ADMIN_CLIENT_CERT = f"{CLI_PEER_ROOT}/crypto/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.crt"
ORDERER_ADMIN_CLIENT_KEY = f"{CLI_PEER_ROOT}/crypto/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.key"

# The `cli` container's default identity (see docker-compose.yml) is
# Hospital_A's admin. Operations that need to act as Hospital_B instead
# override that identity the same way install_chaincode() in
# fabric_commands.sh already does for chaincode approval.
HOSPITAL_B_ENV = [
    "-e", "CORE_PEER_LOCALMSPID=HospitalBMSP",
    "-e", f"CORE_PEER_MSPCONFIGPATH={CLI_PEER_ROOT}/crypto/peerOrganizations/Hospital_B.example.com/users/Admin@Hospital_B.example.com/msp",
    "-e", f"CORE_PEER_TLS_ROOTCERT_FILE={CLI_PEER_ROOT}/crypto/peerOrganizations/Hospital_B.example.com/peers/peer0.Hospital_B.example.com/tls/ca.crt",
    "-e", "CORE_PEER_ADDRESS=peer0.Hospital_B.example.com:7061",
    "-e", "CORE_PEER_TLS_ENABLED=true",
]

CHANNEL_NAME_RE = re.compile(r"^[a-z][a-z0-9.-]{0,248}$")


def _docker_exec(*args: str) -> dict:
    try:
        result = subprocess.run(
            [docker_binary(), "exec", *args],
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
        "stdout": result.stdout.strip()[-4000:],
        "stderr": result.stderr.strip()[-4000:],
    }


def _sync_configtx() -> None:
    """config/configtx.yaml is what configtxgen reads inside `cli` (via its
    ./config bind mount). network_config.yaml is the actual source of
    truth for org/channel topology, so refresh the copy on every run
    instead of trusting one left over from a previous session.
    """
    src = PROJECT_ROOT / "network_config.yaml"
    dst_dir = PROJECT_ROOT / "config"
    dst_dir.mkdir(exist_ok=True)
    if src.exists():
        shutil.copyfile(src, dst_dir / "configtx.yaml")


def _already_exists(result: dict) -> bool:
    text = (result.get("stdout", "") + result.get("stderr", "")).lower()
    return "already exists" in text or "already joined" in text


def _osnadmin_ok(result: dict) -> bool:
    """osnadmin prints "Status: <code>\n{...}" from the admin HTTP API but
    - unlike most Fabric CLI subcommands - exits 0 even when that status is
    a 4xx/5xx error, so `_docker_exec`'s returncode-based "ok" isn't
    trustworthy for it on its own. Fall back to returncode when there's no
    "Status: " line to check (e.g. a different failure mode entirely).
    """
    if not result.get("ok"):
        return False
    match = re.search(r"Status:\s*(\d+)", result.get("stdout", ""))
    if match:
        return match.group(1).startswith("2")
    return True


class ChannelCreateRequest(BaseModel):
    channel: str = "mychannel"


@router.post("/channel/create")
def create_channel(request: ChannelCreateRequest):
    """Bring up `request.channel` end to end: generate its genesis block,
    join it to the orderer via the channel participation API, join both
    hospital peers to it, and update both orgs' anchor peers.

    Best-effort and sequential - every step's result is returned in
    order, so a partial run (e.g. the stack isn't fully up yet) is
    diagnosable from the response instead of a single opaque failure.
    Safe to call again for a channel that already exists: "already
    exists" responses from osnadmin/peer are treated as success for that
    step rather than aborting the whole run.
    """
    channel = request.channel.strip()
    if not CHANNEL_NAME_RE.match(channel):
        return {
            "ok": False, "channel": channel, "steps": [],
            "detail": "Invalid channel name - Fabric channel names must start with a "
                      "lowercase letter and contain only lowercase letters, digits, "
                      "'.' and '-'.",
        }

    _sync_configtx()
    steps: list[dict[str, Any]] = []

    def run(name: str, *args: str) -> dict:
        result = _docker_exec(*args)
        steps.append({"name": name, **result})
        return result

    genesis_block = f"channel-artifacts/{channel}.genesis.block"

    r = run(
        "generate channel genesis block",
        "cli", "configtxgen", "-profile", "TwoOrgsChannel",
        "-channelID", channel, "-outputBlock", genesis_block,
    )
    if not r.get("ok"):
        return {
            "ok": False, "channel": channel, "steps": steps,
            "detail": "configtxgen failed - see the step above for the real error. "
                      "Common causes: crypto-config/ hasn't been generated yet "
                      "(python3 quantum_cryptogen.py), or network_config.yaml doesn't "
                      "define both Hospital_A and Hospital_B under this channel's profile.",
        }

    r = run(
        "join channel to orderer (channel participation API)",
        "cli", "osnadmin", "channel", "join",
        "--channelID", channel, "--config-block", genesis_block,
        "-o", "orderer.example.com:9443",
        "--ca-file", ORDERER_TLS_CA,
        "--client-cert", ORDERER_ADMIN_CLIENT_CERT,
        "--client-key", ORDERER_ADMIN_CLIENT_KEY,
    )
    orderer_joined = _osnadmin_ok(r) or _already_exists(r)
    if not orderer_joined:
        return {
            "ok": False, "channel": channel, "steps": steps,
            "detail": "osnadmin could not join the channel to the orderer - see the "
                      "step above (check for an x509 certificate error specifically: "
                      "that means the orderer's live TLS cert and the CA the channel "
                      "config trusts don't actually match, which no amount of "
                      "retrying will fix - crypto-config needs regenerating).",
        }

    r = run("join Hospital_A peer to channel", "cli", "peer", "channel", "join", "-b", genesis_block)
    hosp_a_joined = r.get("ok") or _already_exists(r)

    r = run(
        "join Hospital_B peer to channel",
        *HOSPITAL_B_ENV, "cli", "peer", "channel", "join", "-b", genesis_block,
    )
    hosp_b_joined = r.get("ok") or _already_exists(r)

    for org, msp_id in ORG_MSP_IDS.items():
        anchor_tx = f"channel-artifacts/{msp_id}anchors_{channel}.tx"
        run(
            f"generate anchor peer update tx ({org})",
            "cli", "configtxgen", "-profile", "TwoOrgsChannel",
            "-outputAnchorPeersUpdate", anchor_tx, "-channelID", channel, "-asOrg", ORG_NAMES[org],
        )

    if hosp_a_joined:
        run(
            "update Hospital_A anchor peers",
            "cli", "peer", "channel", "update",
            "-o", "orderer.example.com:7050", "-c", channel,
            "-f", f"channel-artifacts/HospitalAMSPanchors_{channel}.tx",
            "--tls", "--cafile", ORDERER_TLS_CA,
        )

    if hosp_b_joined:
        run(
            "update Hospital_B anchor peers",
            *HOSPITAL_B_ENV,
            "cli", "peer", "channel", "update",
            "-o", "orderer.example.com:7050", "-c", channel,
            "-f", f"channel-artifacts/HospitalBMSPanchors_{channel}.tx",
            "--tls", "--cafile", ORDERER_TLS_CA,
        )

    return {"ok": orderer_joined and hosp_a_joined and hosp_b_joined, "channel": channel, "steps": steps}


HOSPITAL_A_PEER_TLS_CA = f"{CLI_PEER_ROOT}/crypto/peerOrganizations/Hospital_A.example.com/peers/peer0.Hospital_A.example.com/tls/ca.crt"
HOSPITAL_B_PEER_TLS_CA = f"{CLI_PEER_ROOT}/crypto/peerOrganizations/Hospital_B.example.com/peers/peer0.Hospital_B.example.com/tls/ca.crt"
CHAINCODE_PATH_ROOT = f"{CLI_PEER_ROOT}/chaincode"


def _package_id_from_output(text: str) -> str | None:
    # `calculatepackageid` prints just the bare "<label>:<hash>" id; some
    # other lifecycle subcommands print it as "...identifier: <id>" instead.
    match = re.search(r"identifier:\s*(\S+)", text)
    if match:
        return match.group(1)
    text = text.strip()
    return text or None


class ChaincodeDeployRequest(BaseModel):
    channel: str = "mychannel"
    chaincode: str = "quantum_records"
    version: str = "1.0"
    sequence: int = 1
    path: str | None = None  # defaults to chaincode/<chaincode> inside the cli container


@router.post("/chaincode/deploy")
def deploy_chaincode(request: ChaincodeDeployRequest):
    """Package, install (on both orgs' peers), approve (by both orgs), and
    commit `request.chaincode` on `request.channel`.

    This is the missing half of "channel/create": that endpoint only
    brings the channel itself up (genesis block, orderer join, peer
    joins, anchor peers) - it was never responsible for putting any
    chaincode on the channel, and nothing else in this API ever called
    the chaincode lifecycle commands either. fabric_commands.sh's
    install_chaincode() had the manual steps but was never wired into the
    dashboard, so until this endpoint runs at least once for a given
    channel, `POST /test/blockchain/query` (and any real invoke) has
    nothing installed to query - that's a chaincode-lifecycle gap, not a
    naming/identifier mismatch (chaincode names are validated by a
    separate, more permissive regex than MSP IDs - underscores are
    explicitly allowed in chaincode names, per Fabric's own
    core/chaincode/lifecycle validation).

    Best-effort and sequential, same shape as channel/create: every
    step's result is returned in order. Re-running is mostly safe - the
    install steps tolerate "already successfully installed", and
    approve/commit tolerate messages indicating that sequence is already
    defined - but note a real *change* to already-committed chaincode
    (new code, different endorsement policy) needs the sequence number
    bumped, which the dashboard doesn't currently expose beyond the
    request body's `sequence` field.
    """
    channel = request.channel.strip()
    chaincode = request.chaincode.strip()
    version = request.version.strip()
    sequence = str(request.sequence)
    label = f"{chaincode}_{version}"
    package_path = request.path or f"{CHAINCODE_PATH_ROOT}/{chaincode}"
    package_file = f"{chaincode}.tar.gz"

    steps: list[dict[str, Any]] = []

    def run(name: str, *args: str) -> dict:
        result = _docker_exec(*args)
        steps.append({"name": name, **result})
        return result

    def already_installed(result: dict) -> bool:
        text = (result.get("stdout", "") + result.get("stderr", "")).lower()
        return "already successfully installed" in text

    r = run(
        "package chaincode",
        "cli", "peer", "lifecycle", "chaincode", "package", package_file,
        "--path", package_path, "--lang", "golang", "--label", label,
    )
    if not r.get("ok"):
        return {
            "ok": False, "channel": channel, "chaincode": chaincode, "steps": steps,
            "detail": "peer lifecycle chaincode package failed - see the step above. "
                      "Common cause: the chaincode's Go module dependencies aren't "
                      "reachable/vendored for a build inside the cli container.",
        }

    r = run("install on Hospital_A peer", "cli", "peer", "lifecycle", "chaincode", "install", package_file)
    hosp_a_installed = r.get("ok") or already_installed(r)

    r = run(
        "install on Hospital_B peer",
        *HOSPITAL_B_ENV, "cli", "peer", "lifecycle", "chaincode", "install", package_file,
    )
    hosp_b_installed = r.get("ok") or already_installed(r)

    if not (hosp_a_installed and hosp_b_installed):
        return {
            "ok": False, "channel": channel, "chaincode": chaincode, "steps": steps,
            "detail": "Chaincode install failed on at least one peer - see the steps above.",
        }

    r = run("calculate package ID", "cli", "peer", "lifecycle", "chaincode", "calculatepackageid", package_file)
    package_id = _package_id_from_output(r.get("stdout", "")) if r.get("ok") else None
    if not package_id:
        return {
            "ok": False, "channel": channel, "chaincode": chaincode, "steps": steps,
            "detail": "Could not determine the package ID from calculatepackageid - see the step above.",
        }

    r = run(
        "approve for Hospital_A",
        "cli", "peer", "lifecycle", "chaincode", "approveformyorg",
        "-o", "orderer.example.com:7050",
        "--channelID", channel, "--name", chaincode, "--version", version,
        "--package-id", package_id, "--sequence", sequence,
        "--tls", "--cafile", ORDERER_TLS_CA,
    )
    hosp_a_approved = r.get("ok") or _already_exists(r)

    r = run(
        "approve for Hospital_B",
        *HOSPITAL_B_ENV,
        "cli", "peer", "lifecycle", "chaincode", "approveformyorg",
        "-o", "orderer.example.com:7050",
        "--channelID", channel, "--name", chaincode, "--version", version,
        "--package-id", package_id, "--sequence", sequence,
        "--tls", "--cafile", ORDERER_TLS_CA,
    )
    hosp_b_approved = r.get("ok") or _already_exists(r)

    r = run(
        "commit chaincode definition",
        "cli", "peer", "lifecycle", "chaincode", "commit",
        "-o", "orderer.example.com:7050",
        "--channelID", channel, "--name", chaincode, "--version", version, "--sequence", sequence,
        "--tls", "--cafile", ORDERER_TLS_CA,
        "--peerAddresses", "peer0.Hospital_A.example.com:7051",
        "--tlsRootCertFiles", HOSPITAL_A_PEER_TLS_CA,
        "--peerAddresses", "peer0.Hospital_B.example.com:7061",
        "--tlsRootCertFiles", HOSPITAL_B_PEER_TLS_CA,
    )
    committed = r.get("ok") or _already_exists(r)

    ok = hosp_a_approved and hosp_b_approved and committed
    return {
        "ok": ok,
        "channel": channel,
        "chaincode": chaincode,
        "version": version,
        "sequence": request.sequence,
        "package_id": package_id,
        "steps": steps,
    }


@router.get("/channel/{channel}/status")
def channel_status(channel: str):
    """Whether the orderer (via the channel participation API) and each
    hospital peer actually have this channel - a quick way to check
    "did channel/create actually take" without re-running it.
    """
    orderer = _docker_exec(
        "cli", "osnadmin", "channel", "list",
        "-o", "orderer.example.com:9443",
        "--ca-file", ORDERER_TLS_CA,
        "--client-cert", ORDERER_ADMIN_CLIENT_CERT,
        "--client-key", ORDERER_ADMIN_CLIENT_KEY,
    )
    peer_a = _docker_exec("cli", "peer", "channel", "getinfo", "-c", channel)
    peer_b = _docker_exec(*HOSPITAL_B_ENV, "cli", "peer", "channel", "getinfo", "-c", channel)
    return {"channel": channel, "orderer": orderer, "Hospital_A": peer_a, "Hospital_B": peer_b}
