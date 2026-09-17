# Embedded ops-agent architecture: a standalone, scoped, self-healing watcher

This describes the pattern behind `agents/ops_agent/agent.py` in the reference project - a script that any
process (a cron job, a scheduled task, a person's terminal) can invoke to watch a docker-compose stack and
decide, on its own, whether to notify a human or take one narrow, safe recovery action.

## Why standalone, not a Claude Code/Cowork session

The point of this pattern is that the agent is part of the project's own infrastructure - something that
keeps working whether or not any interactive Claude session is open. It makes its own calls to the Anthropic
API directly (needs its own `ANTHROPIC_API_KEY`), with a fixed system prompt encoding its policy. It does not
share memory or context with whatever Claude session built or deployed it - each invocation is a fresh,
independent reasoning pass.

## The tool boundary is the safety mechanism, not a suggestion

Give the agent a small, fixed set of tools, and make sure the *mutating* ones are structurally incapable of
anything destructive - don't rely on the system prompt alone to stop it. In the reference implementation:

- `get_status`, `get_topology`, `get_logs` - read-only, hit your management API's own endpoints rather than
  shelling out to `docker` directly (reuse whatever PATH/credential-resolution fix your API already has -
  don't solve that problem twice).
- `bring_up` - the *only* mutating action. It should only ever be able to run a scoped `docker compose up -d
  [specific services]`. No tool for `down`, `rm`, `stop`, or anything that could destroy state or data. This
  is worth stating explicitly in the system prompt as a hard boundary ("you have no tool that can stop,
  remove, or tear down anything"), not just an instruction to be careful.
- `load_state` / `save_state` - a small persisted JSON file, so the agent remembers what it's already flagged
  across runs.
- `notify` - the only way it communicates anything back to a human. Wire this into wherever your team will
  actually see it (a dashboard panel, a Slack webhook, etc.) - an agent that "notifies" into a void it prints
  to and nobody reads is not actually safer than one with no notify tool at all.

## The state machine: notify once, autofix only the known-safe case

A naive version of this either spams on every run (nagging) or silently retries a failing service forever
(hiding a real crash loop). The policy that's worked in practice:

1. **Not previously seen:** record it, notify, and stop - don't act on the first sighting. This gives a human
   a chance to see and react before the agent does anything.
2. **Previously seen, same problem, not yet autofixed:** if and only if the container's state is the
   known-safe case (e.g. "Created" - defined but never started, usually just a prior `compose up` that got
   interrupted), attempt the one scoped recovery action, re-check, and notify what happened either way. For
   any other bad state (exited, crash-looping/restarting), never auto-restart - notify that it's still broken
   and needs a human look. A container that keeps crashing is a different problem than one that just never
   started, and treating them the same hides real bugs behind an infinite restart loop.
3. **Previously autofixed but still unhealthy:** notify that the fix didn't hold, and stop trying on that
   container - never loop on the same fix more than once per problem.
4. **Previously seen, now healthy:** clear it from state quietly. Silence is the correct output for "nothing's
   wrong" - don't notify just to say everything's fine every single run.

## The guidance channel: how a human steers it without editing code

Once the agent exists, the next problem is that its policy is fixed at write time - if you learn something new
about the stack (e.g. "this particular service is *supposed* to exit cleanly, don't flag it"), you either have
to edit the script every time, or live with a false positive forever. The fix is a small shared file the agent
reads before each run:

```python
GUIDANCE_PATH = Path(__file__).resolve().parent / ".guidance.json"

def load_guidance() -> list[dict]:
    if not GUIDANCE_PATH.exists():
        return []
    try:
        return json.loads(GUIDANCE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []

def consume_guidance(entries: list[dict]) -> None:
    # one-time notes are used up by being included in a run; sticky ones persist
    save_guidance([e for e in entries if e.get("sticky")])
```

Each entry has a `message`, a `sticky` flag, and metadata (`added_by`, `added_at`). Fold the current entries
into the *user* message that starts a run (not the system prompt - keep the system prompt as the stable,
permanent policy/tool-boundary definition, and treat guidance as situational context layered on top each
run). A one-time note gets consumed after being read once; a sticky note is a standing rule that persists
until explicitly removed. Critically: guidance only ever adds context to what the model reasons about - it
should never be able to expand the tool boundary above. State this explicitly in the system prompt so a
malformed or even adversarial note can't talk the agent into anything the tool boundary itself doesn't already
allow.

Expose this file's contents through your management API (`GET/POST/DELETE` on a `/agent/guidance`-style
endpoint) so both a human at a dashboard and an orchestrating Claude session can leave notes, not just someone
editing a JSON file by hand.

## Wiring the agent to actually run

A script sitting in a repo doesn't watch anything by itself. Two complementary ways to trigger it, both worth
having:
- **On a schedule**, independent of any interactive session (a scheduled task, cron, launchd) - this is what
  makes it "self-healing over time" rather than "something I remember to run."
- **On demand from a dashboard button**, which is genuinely useful for a human to sanity-check things right
  now, but is a real API call each time (cost + latency) - say so in the UI rather than implying it's free or
  instant.
