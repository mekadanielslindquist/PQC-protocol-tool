"""Operational endpoints for driving and monitoring the docker-compose
stack itself - not service business logic, just the deployment surface.

This exists because raw `docker`/`docker compose` access from wherever
this API happens to run is unreliable in exactly the ways that matter for
automation: PATH resolution (see docker_binary() in config.py), the
Docker Desktop daemon on macOS periodically becoming unresponsive to
some operations while others keep working, and `docker compose logs`/`ps`
needing a real timeout so a caller (human or agent) gets a clear signal
instead of hanging indefinitely.

Every endpoint here is read-only or an idempotent `docker compose up`,
scoped to explicit services - nothing here can `down`/`rm`/delete
anything. That's a deliberate boundary: an agent (or a scheduled health
check) driving deployments through this API can recover a stuck service
without ever being able to destroy data or state.
"""
from __future__ import annotations

import json
import subprocess
import time

from fastapi import APIRouter

from management_api.config import PROJECT_ROOT, docker_binary

router = APIRouter(prefix="/ops", tags=["ops"])

UP_LOG_PATH = PROJECT_ROOT / "management_api" / ".ops_up.log"
AGENT_STATE_PATH = PROJECT_ROOT / "agents" / "ops_agent" / ".alert_state.json"
AGENT_GUIDANCE_PATH = PROJECT_ROOT / "agents" / "ops_agent" / ".guidance.json"
AGENT_RUN_TIMEOUT = 90


@router.get("/status")
def status():
    """Real per-container state via `docker ps -a` - not the TCP-probe
    fallback /api/services uses, the actual state Docker itself reports
    (Created / Up ... (healthy) / Up ... (unhealthy) / Exited (n) /
    Restarting). This is what a monitoring agent should poll: a service
    can be "up" on its published port from a stale proxy while the real
    container is gone (we hit this for real tonight), so port reachability
    alone is not trustworthy for automation.
    """
    db = docker_binary()
    try:
        r = subprocess.run(
            [db, "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        return {"ok": False, "detail": "Docker CLI not found on this machine."}
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "detail": "docker ps timed out after 15s - the Docker daemon is likely unresponsive "
                      "(this has happened repeatedly on this machine; a manual Docker Desktop "
                      "restart has always cleared it, an agent should not attempt to fix this itself).",
        }

    if r.returncode != 0:
        return {"ok": False, "detail": r.stderr.strip()[:2000]}

    containers = []
    for line in r.stdout.strip().splitlines():
        if not line.strip():
            continue
        name, _, raw_status = line.partition("\t")
        status_lower = raw_status.lower()
        healthy = "(healthy)" in status_lower
        unhealthy = "(unhealthy)" in status_lower
        containers.append({
            "name": name,
            "status": raw_status,
            "state": (
                "created" if raw_status.startswith("Created") else
                "exited" if raw_status.startswith("Exited") else
                "restarting" if raw_status.startswith("Restarting") else
                "up" if raw_status.startswith("Up") else
                "unknown"
            ),
            "healthy": True if healthy else False if unhealthy else None,
        })

    return {"ok": True, "containers": containers}


@router.get("/logs/{service}")
def logs(service: str, tail: int = 80):
    """`docker compose logs <service> --tail=N`, read-only. Times out
    fast (15s) rather than hanging if the daemon is wedged, so a caller
    gets a clear signal instead of an indefinite hang.
    """
    tail = max(1, min(tail, 2000))
    try:
        result = subprocess.run(
            [docker_binary(), "compose", "logs", service, "--no-color", f"--tail={tail}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return {"ok": False, "detail": "Docker CLI not found on this machine."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "docker compose logs timed out after 15s - the Docker daemon may be unresponsive."}

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-4000:],
    }


@router.get("/inspect/{name}")
def inspect(name: str):
    """Raw `docker inspect` State block (Status/Health/ExitCode/etc.) for
    one real container name, as /ops/status's `name` field reports it.
    """
    db = docker_binary()
    try:
        r = subprocess.run(
            [db, "inspect", name, "--format", "{{json .State}}"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "docker inspect timed out after 15s - the Docker daemon may be unresponsive."}

    if r.returncode != 0:
        return {"ok": False, "detail": r.stderr.strip()[:2000]}
    return {"ok": True, "state": r.stdout.strip()}


@router.get("/find_lib/{image}")
def find_lib(image: str, pattern: str = "libssl*"):
    """Run `find /usr/lib -iname <pattern>` inside a built image via a
    throwaway `docker run --rm`, read-only. For chasing down a missing
    shared library without needing a shell in the container."""
    db = docker_binary()
    try:
        r = subprocess.run(
            [db, "run", "--rm", "--entrypoint", "find", image, "/usr/lib", "/usr/lib64", "-iname", pattern],
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "timed out after 20s"}
    return {"ok": True, "returncode": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()[:1500]}


@router.post("/up")
def up(services: str = "", build: bool = False):
    """Kick off `docker compose up -d [--build] [services...]` in the
    background (image builds and healthchecks can take well over a
    minute) and return immediately - poll /ops/status and /ops/up_log
    afterward to see progress.

    Pass ?services=a,b,c to scope it to exactly those services and their
    dependencies - this matters in practice: a blanket `up -d` touches
    every service, and one unrelated container hanging on its own
    stop/recreate can block the whole run from ever reaching the service
    you actually care about. Omit services for a full `up -d`.

    Pass ?build=true to rebuild those services' images first (e.g. after
    a Dockerfile fix that hasn't been picked up yet).
    """
    db = docker_binary()
    svc_list = [s.strip() for s in services.split(",") if s.strip()]
    cmd = [db, "compose", "up", "-d"]
    if build:
        cmd.append("--build")
    cmd += svc_list
    with open(UP_LOG_PATH, "w") as logf:
        subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
    return {"started": True, "services": svc_list or "all", "build": build}


@router.get("/up_log")
def up_log():
    """Tail the log from the most recent /ops/up call. Note: `docker
    compose`'s own stdout is block-buffered when piped (not a TTY), so
    this can appear frozen for a while even when real progress is
    happening underneath - cross-check against /ops/status rather than
    treating a stalled log as a stalled operation."""
    if not UP_LOG_PATH.exists():
        return {"exists": False}
    return {"exists": True, "content": UP_LOG_PATH.read_text()[-6000:]}


@router.get("/agent/state")
def agent_state():
    """Read-only: agents/ops_agent's persisted alert-tracking state (which
    containers it's currently watching, when each was first seen, whether
    it already tried an autofix). This is the same file the agent itself
    reads/writes via its load_state/save_state tools - this endpoint just
    exposes it to the dashboard. Empty dict if the agent has never run or
    everything was healthy as of its last run.
    """
    if not AGENT_STATE_PATH.exists():
        return {"ok": True, "state": {}, "last_run": None}
    try:
        state = json.loads(AGENT_STATE_PATH.read_text())
    except json.JSONDecodeError:
        return {"ok": False, "detail": "state file exists but is not valid JSON"}
    return {"ok": True, "state": state, "last_run": AGENT_STATE_PATH.stat().st_mtime}


@router.post("/agent/run")
def agent_run():
    """Run one real ops_agent check-and-act pass: `python3 -m
    agents.ops_agent.agent --check`. This is a genuine call to the
    Anthropic API, not a simulation - it costs a few seconds of latency
    and real API usage each time it's invoked, same as running it from a
    terminal. Whatever it prints via its notify tool comes back as
    stdout. The agent's own tool boundary (see agent.py's SYSTEM_PROMPT)
    means the only mutating thing it can ever do is a scoped, non-
    destructive `docker compose up -d` - this endpoint doesn't add any
    capability beyond what the script already has.
    """
    try:
        r = subprocess.run(
            ["python3", "-m", "agents.ops_agent.agent", "--check"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=AGENT_RUN_TIMEOUT,
        )
    except FileNotFoundError:
        return {"ok": False, "detail": "python3 not found on this machine."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": f"ops_agent did not finish within {AGENT_RUN_TIMEOUT}s."}

    return {
        "ok": r.returncode == 0,
        "returncode": r.returncode,
        "stdout": r.stdout.strip(),
        "stderr": r.stderr.strip()[:2000],
    }


@router.get("/agent/guidance")
def agent_guidance_list():
    """Read-only: current guidance entries waiting for ops_agent's next
    run(s) - the same file agent.py's load_guidance()/save_guidance()
    read and write, just exposed here for the dashboard.
    """
    if not AGENT_GUIDANCE_PATH.exists():
        return {"ok": True, "entries": []}
    try:
        entries = json.loads(AGENT_GUIDANCE_PATH.read_text())
    except json.JSONDecodeError:
        return {"ok": False, "detail": "guidance file exists but is not valid JSON"}
    return {"ok": True, "entries": entries if isinstance(entries, list) else []}


@router.post("/agent/guidance")
def agent_guidance_add(message: str, sticky: bool = False, added_by: str = "dashboard"):
    """Leave a note for ops_agent's next --check run to read. A one-time
    note (sticky=false, the default) is folded into the next run's
    context and then consumed; a sticky note stays until deleted, for
    standing context rather than a one-off heads-up. This only ever adds
    context to what the agent reasons about - it can't grant it any
    tool/capability it doesn't already have (see agent.py's
    SYSTEM_PROMPT and its fixed TOOLS list).
    """
    entries = []
    if AGENT_GUIDANCE_PATH.exists():
        try:
            entries = json.loads(AGENT_GUIDANCE_PATH.read_text())
        except json.JSONDecodeError:
            entries = []
    entry = {
        "id": f"g_{int(time.time() * 1000):x}",
        "added_at": time.time(),
        "added_by": added_by,
        "message": message,
        "sticky": sticky,
    }
    entries.append(entry)
    AGENT_GUIDANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_GUIDANCE_PATH.write_text(json.dumps(entries, indent=2))
    return {"ok": True, "entry": entry}


@router.delete("/agent/guidance/{guidance_id}")
def agent_guidance_delete(guidance_id: str):
    """Remove one guidance entry before ops_agent ever reads it (a typo,
    or a note that's no longer relevant)."""
    if not AGENT_GUIDANCE_PATH.exists():
        return {"ok": True, "removed": False}
    try:
        entries = json.loads(AGENT_GUIDANCE_PATH.read_text())
    except json.JSONDecodeError:
        return {"ok": False, "detail": "guidance file exists but is not valid JSON"}
    remaining = [e for e in entries if e.get("id") != guidance_id]
    removed = len(remaining) != len(entries)
    AGENT_GUIDANCE_PATH.write_text(json.dumps(remaining, indent=2))
    return {"ok": True, "removed": removed}
