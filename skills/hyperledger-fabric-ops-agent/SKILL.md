---
name: hyperledger-fabric-ops-agent
description: >
  Deploy and operate a docker-compose-based Hyperledger Fabric stack (peers, orderer, CLI, CouchDB) plus companion
  services (Asterisk, libp2p, MQTT, custom crypto services) on Docker Desktop for macOS, and stand up a scoped,
  non-destructive AI agent that watches the stack and reports or recovers safely. Use this whenever deploying,
  debugging, or operating a Hyperledger Fabric / docker-compose stack on a Mac - especially when hitting
  "docker: command not found" from a Python/FastAPI/Node process even though `docker` works fine in a terminal,
  `docker compose` or `docker inspect` commands hanging or timing out unpredictably while other docker commands
  stay fast, a service stuck in "Created" state after `docker compose up`, a "shared library not found" error
  inside a container whose image just built cleanly, or when asked to build an autonomous/AI agent to monitor
  and self-heal a docker-compose deployment over time.
---

# Hyperledger Fabric + docker-compose ops on macOS, with an embedded AI ops agent

This skill exists because a real deployment of this shape - Hyperledger Fabric plus several companion
services, driven through a management API and optionally an embedded AI agent, on Docker Desktop for macOS -
runs into the same handful of environment-level failure modes over and over, independent of the specific
project. They aren't Fabric bugs and they aren't application bugs; they're about how macOS, Docker Desktop,
and a long-running Python process interact. Diagnosing them from scratch the first time can eat hours, because
the symptoms (a generic "crypto-config hasn't been generated" hint, a 500 error with no detail, a container
that "should" be running but isn't) point away from the real cause. This skill's job is to make the second,
third, and hundredth deployment faster than the first one, by naming these failure modes up front.

Read `references/docker-desktop-macos-gotchas.md` before debugging anything that smells environmental (PATH,
hanging commands, missing libraries at runtime) - check it first, it will usually save you the diagnostic
loop. Read `references/ops-agent-architecture.md` before building or extending an embedded agent that watches
this kind of stack - it lays out a tool-boundary pattern that keeps an agent genuinely safe to run
unsupervised, plus a "guidance channel" pattern for letting a human steer its behavior over time without
editing its code.

## The shape of the problem

A `docker compose` stack for something like this typically has: Fabric peers + orderer + CLI + CouchDB (the
blockchain layer), a handful of custom services that wrap external protocols (SIP/Asterisk, MQTT, libp2p) with
project-specific crypto, and a management API (FastAPI/uvicorn or similar) that is NOT itself in Docker - it
runs directly on the Mac and shells out to `docker`/`docker compose` to drive and inspect the stack. That last
detail is the source of most of the pain: a bare terminal has a login shell's PATH, but a GUI-launched or
IDE-launched Python process often doesn't, and Docker Desktop's daemon has its own, separate failure mode that
has nothing to do with your code. See the reference file for the specific fixes; the short version:

1. **Resolve the `docker` binary's absolute path yourself, once, with a cached helper** - don't rely on bare
   `"docker"` resolving via PATH in a subprocess call from a management API.
2. **Give every docker subprocess call a real timeout**, and treat a timeout as "the daemon may be wedged,"
   not as your code being broken - this is a known, recurring Docker Desktop behavior, not something to chase
   in your own logic.
3. **Never let an agent try to fix a wedged daemon itself.** The only fix anyone has found is a human
   force-quitting Docker Desktop (via Activity Monitor, not a graceful quit - that hangs the same way) and
   reopening it. An agent's job here is to notice and say so clearly, not to retry harder.
4. **Search for library paths at runtime instead of hardcoding one** when a Dockerfile's final stage needs to
   find/symlink a shared library - Debian's multiarch layout means the real path is
   `/usr/lib/<triplet>/libfoo.so`, not bare `/usr/lib/libfoo.so`, and the triplet varies by architecture.

## Building the embedded ops agent

The pattern that works is a small standalone script (not a Claude Code session, not a Cowork trigger) that
calls the Anthropic API directly, talks to your management API's read-only/scoped endpoints rather than
shelling out to Docker itself, and has a deliberately narrow tool set: read status/logs/topology, one scoped
non-destructive recovery action, and a way to notify a human. See
`references/ops-agent-architecture.md` for the full pattern, including:

- why the agent should persist its own alert state and only notify once per new problem, not every run
- which failure states are safe to auto-recover (a container stuck in "Created" - a `compose up` that never
  finished) versus which should always just be reported (anything exited/crash-looping - auto-restarting that
  can hide a real bug)
- the "guidance channel" - a small JSON file the agent reads before each run, letting a human (or an
  orchestrating Claude session) leave it notes ("this exit code is expected, don't flag it" as a standing
  note, or "watch this service closely after today's fix" as a one-time nudge) without ever touching its code
  or expanding what it's allowed to do

## Wiring it into a dashboard

If there's already a management API + dashboard, add a small panel rather than a separate app: a status
summary (badge + count of tracked issues), a "run check now" button that calls a real endpoint (not a
simulation - say so in the UI, since it costs a real API call), and a guidance list with add/remove. Keep the
guidance and state endpoints read-mostly and simple - they're just exposing the same JSON files the agent
script itself reads and writes, not new logic.

## This repo as a worked example

This project (`PQC-protocol-tool`) is the reference implementation this skill was extracted from:
`management_api/config.py`'s `docker_binary()`, `management_api/routers/ops.py`'s timeout-guarded endpoints,
and `agents/ops_agent/agent.py` (including its guidance channel) are all real, working code, not illustrations
- read them directly rather than reimplementing from this skill's description alone. The README's
"Troubleshooting Known Issues" section also documents the Fabric-specific bugs hit along the way (config
identifier validation, channel participation API quirks, x509 chain issues) that are specific to Fabric rather
than to the macOS/Docker environment - worth a read too, but that's a different category of issue than what
this skill covers.
