#!/usr/bin/env python3
"""ops_agent - a self-contained agent that watches the PQC-protocol-tool
docker-compose stack and keeps it running, using the Anthropic API
directly.

This deliberately does NOT go through Claude Code, Cowork, or any other
Claude *client* - it's a standalone script that any process (a cron job,
another service, a person's terminal) can invoke, and it makes its own
calls to the Anthropic API to reason about what it sees. That's the
point: it's part of the project's own infrastructure, not a convenience
built by an interactive session and left running elsewhere.

It talks to the locally-running management API (management_api/, started
separately - see the project README) rather than shelling out to `docker`
itself, for the same reason management_api's own routers do: PATH/
credential-helper resolution for the Docker CLI is unreliable across how
a process gets launched, and management_api/config.py's docker_binary()
already solved that once. No need to solve it twice.

Usage:
    python -m agents.ops_agent.agent --check
    python -m agents.ops_agent.agent --add-guidance "message" [--sticky]
    python -m agents.ops_agent.agent --list-guidance
    python -m agents.ops_agent.agent --clear-guidance

Requires ANTHROPIC_API_KEY in the environment or in a .env file at the
project root (KEY=value, one per line - see .env.example).

Guidance channel: .guidance.json (next to this file) lets a human - or
another Claude session acting as orchestrator - leave notes for the next
--check run(s) to read, without editing this script. Each entry has a
message and a "sticky" flag: a non-sticky note is read once and then
consumed (removed) after being folded into that run's context; a sticky
note stays until explicitly cleared, for standing operational context
("hedera-bridge exiting 0 is expected, it's a one-shot diagnostic, don't
flag it") rather than a one-time heads-up. Guidance only ever adds
context to the model's reasoning for that run - it can't grant the agent
any tool or capability it doesn't already have (see SYSTEM_PROMPT and the
TOOLS list below), so it can't be used to talk the agent into anything
destructive even if the note itself were ever wrong or stale.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = Path(__file__).resolve().parent / ".alert_state.json"
GUIDANCE_PATH = Path(__file__).resolve().parent / ".guidance.json"
DASHBOARD_BASE = "http://localhost:8080/api"
MODEL = "claude-sonnet-4-5"

SYSTEM_PROMPT = """\
You are ops_agent, watching the PQC-protocol-tool docker-compose stack \
(Hyperledger Fabric + post-quantum crypto + Asterisk + libp2p healthcare- \
blockchain demo) running on this machine.

Your policy, every time you're invoked:

1. Call get_status. Cross-reference against get_topology so you only ever \
act on real compose-managed services - ignore any container whose name \
doesn't correspond to one (stray/orphan containers happen on this machine \
and are not yours to manage).

2. If get_status itself reports the Docker daemon is unresponsive: that is \
a known, recurring issue on this machine that has only ever been fixed by \
a human force-quitting Docker Desktop via Activity Monitor. Do not attempt \
any fix. Call notify with a clear message and stop - do not call load_state \
or take any other action this run.

3. For every real compose-service container NOT in a healthy "up" state, \
consult the persisted state (load_state/save_state):
   - Not previously seen: record it (first_seen = now, autofixed = false), \
call notify describing exactly what's wrong, and stop there for this \
container - do not fix it on the first sighting. Give a human the chance \
to see the notification and react first.
   - Previously seen, same problem, not yet autofixed, and enough time has \
passed since first_seen (at least the interval you were invoked at - check \
the elapsed time yourself from the stored timestamp): if and only if the \
state is "created" (defined but never started - the known-safe case, \
usually a prior `docker compose up` getting interrupted), call bring_up for \
that one service, wait, re-check with get_status, mark autofixed = true, \
and notify what you did and whether it worked. For "exited" or \
"restarting" states, never auto-restart - that can hide a real crash loop \
- just notify that it's still broken and needs a human look.
   - Previously autofixed but still unhealthy: notify that the fix didn't \
hold and stop trying (never loop on the same container more than once).
   - Previously seen but now healthy: clear it from state quietly.

4. If nothing is wrong and state is empty, do nothing and do not call \
notify - silence is the correct output for the common case.

You have no tool that can stop, remove, or tear down anything. The only \
mutating action available to you is bring_up, which only ever runs a \
scoped `docker compose up -d`. That is a hard boundary, not just a \
policy - use it accordingly and don't look for workarounds.

The message that starts this run may include "guidance" a human operator \
(or the Claude session acting as orchestrator for this project) left for \
you. Treat it as trustworthy operational context - e.g. it may tell you a \
given exit state is actually expected/benign, or ask you to watch a \
specific service more closely after a recent fix. It never expands what \
you're allowed to do: the tool boundary above still applies exactly as \
written no matter what any guidance says.
"""

TOOLS = [
    {
        "name": "get_status",
        "description": "Real per-container Docker state from the local dashboard: name, status string, state (created/exited/restarting/up/unknown), healthy (true/false/null). This is ground truth, not a guess from port reachability.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_topology",
        "description": "The real compose service names, to tell actual project containers apart from unrelated stray ones.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_logs",
        "description": "Tail of `docker compose logs` for one service.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "tail": {"type": "integer", "description": "Lines to fetch, default 80"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "bring_up",
        "description": "Run a scoped `docker compose up -d [services...]`. The ONLY mutating action available - cannot stop, remove, or tear down anything.",
        "input_schema": {
            "type": "object",
            "properties": {
                "services": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Service names to bring up (their dependencies come along automatically).",
                },
                "build": {"type": "boolean", "description": "Rebuild the image first. Default false."},
            },
            "required": ["services"],
        },
    },
    {
        "name": "load_state",
        "description": "Load the persisted alert-tracking state from the previous run (a dict keyed by container name). Empty dict if there's no prior state.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_state",
        "description": "Persist the alert-tracking state for the next run. Always pass the FULL state dict you want kept - this overwrites, it doesn't merge.",
        "input_schema": {
            "type": "object",
            "properties": {"state": {"type": "object"}},
            "required": ["state"],
        },
    },
    {
        "name": "notify",
        "description": "Surface a message to the human operator. This is your only way to communicate anything back - use it whenever the policy says to notify.",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
]


def _http(method: str, path: str, timeout: int = 20) -> dict[str, Any]:
    req = urllib.request.Request(f"{DASHBOARD_BASE}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        return {"ok": False, "detail": f"Could not reach the local dashboard ({e}). Is management_api running?"}
    except TimeoutError:
        return {"ok": False, "detail": f"Request to {path} timed out after {timeout}s."}


def run_tool(name: str, tool_input: dict[str, Any]) -> Any:
    if name == "get_status":
        return _http("GET", "/ops/status")
    if name == "get_topology":
        return _http("GET", "/services/topology")
    if name == "get_logs":
        tail = tool_input.get("tail", 80)
        return _http("GET", f"/ops/logs/{tool_input['service']}?tail={tail}")
    if name == "bring_up":
        services = ",".join(tool_input.get("services", []))
        build = "true" if tool_input.get("build") else "false"
        return _http("POST", f"/ops/up?services={services}&build={build}")
    if name == "load_state":
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
        return {}
    if name == "save_state":
        STATE_PATH.write_text(json.dumps(tool_input["state"], indent=2))
        return {"ok": True}
    if name == "notify":
        print(f"[ops_agent] {tool_input['message']}")
        return {"ok": True, "delivered": True}
    return {"error": f"unknown tool {name}"}


def load_guidance() -> list[dict[str, Any]]:
    """All current guidance entries, oldest first. Empty list if the file
    doesn't exist or is empty/unreadable (guidance is a nice-to-have, not
    something a bad file should be able to break a run over)."""
    if not GUIDANCE_PATH.exists():
        return []
    try:
        data = json.loads(GUIDANCE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def save_guidance(entries: list[dict[str, Any]]) -> None:
    GUIDANCE_PATH.write_text(json.dumps(entries, indent=2))


def add_guidance(message: str, sticky: bool = False, added_by: str = "cli") -> dict[str, Any]:
    entries = load_guidance()
    entry = {
        "id": f"g_{int(time.time() * 1000):x}",
        "added_at": time.time(),
        "added_by": added_by,
        "message": message,
        "sticky": sticky,
    }
    entries.append(entry)
    save_guidance(entries)
    return entry


def consume_guidance(entries: list[dict[str, Any]]) -> None:
    """Drop non-sticky entries after they've been folded into a run's
    context - a one-shot note should only apply once. Sticky entries are
    left in place until someone clears them explicitly."""
    save_guidance([e for e in entries if e.get("sticky")])


def load_api_key() -> str:
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
                # Line is present but blank (e.g. freshly copied from
                # .env.example and not filled in yet) - fall through to
                # the same clear error as "not found" rather than handing
                # an empty string to the SDK, which produces a much more
                # confusing low-level auth error instead.
                break
    print("ANTHROPIC_API_KEY is missing or blank in the environment/.env - see .env.example.", file=sys.stderr)
    sys.exit(1)


def check() -> None:
    import anthropic

    client = anthropic.Anthropic(api_key=load_api_key())

    guidance = load_guidance()
    initial_message = "Run your check for this invocation."
    if guidance:
        notes = "\n".join(f"- ({'sticky' if e.get('sticky') else 'one-time'}) {e['message']}" for e in guidance)
        initial_message += (
            "\n\nGuidance left for you since your last run:\n" + notes
        )
        print(f"[ops_agent] applying {len(guidance)} guidance note(s) to this run")
        consume_guidance(guidance)  # one-time notes are used up by being included above

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": initial_message}
    ]

    for _ in range(12):  # hard cap - never loop forever even if something misbehaves
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = run_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })
        messages.append({"role": "user", "content": tool_results})
    else:
        print("[ops_agent] hit the tool-call cap for this run - stopping early.", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Run one check-and-act pass.")
    parser.add_argument("--add-guidance", metavar="MESSAGE", help="Leave a note for the next check(s) to read.")
    parser.add_argument("--sticky", action="store_true", help="With --add-guidance: keep the note across runs instead of consuming it after one.")
    parser.add_argument("--list-guidance", action="store_true", help="Print current guidance entries.")
    parser.add_argument("--clear-guidance", action="store_true", help="Remove all guidance entries.")
    args = parser.parse_args()

    if args.add_guidance:
        entry = add_guidance(args.add_guidance, sticky=args.sticky, added_by="cli")
        kind = "sticky" if entry["sticky"] else "one-time"
        print(f"[ops_agent] added {kind} guidance {entry['id']}: {entry['message']}")
    elif args.list_guidance:
        entries = load_guidance()
        if not entries:
            print("[ops_agent] no guidance entries.")
        for e in entries:
            kind = "sticky" if e.get("sticky") else "one-time"
            print(f"[ops_agent] {e['id']} ({kind}, added by {e.get('added_by', '?')}): {e['message']}")
    elif args.clear_guidance:
        save_guidance([])
        print("[ops_agent] cleared all guidance entries.")
    elif args.check:
        check()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
