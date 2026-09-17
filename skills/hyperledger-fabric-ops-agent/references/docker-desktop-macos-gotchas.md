# Docker Desktop on macOS: gotchas for a process driving `docker compose`

These are environment-level failure modes, not application bugs. All of them were hit for real building this
project's management API and ops agent - the fixes below are the ones that actually worked, not theoretical.

## 1. `docker` not found from a subprocess call, even though it works in a terminal

**Symptom:** a Python (or Node, etc.) process shells out to `["docker", ...]` and gets `FileNotFoundError` or a
generic "Docker CLI not found," while running `docker` directly in a terminal works fine. Any error message
your own code generates downstream of this (e.g. "crypto-config hasn't been generated yet" from a
`configtxgen` wrapper) will look like a totally unrelated problem - it isn't, it's the same root cause wearing
a different hat.

**Why:** a login shell's PATH includes `~/.docker/bin` (where Docker Desktop puts the CLI) via shell rc files.
A process launched from a GUI, an IDE run configuration, or certain non-login shell contexts often doesn't
inherit that PATH.

**Fix:** resolve the binary's absolute path once, with a small cached helper, and use the resolved path
everywhere instead of the bare string `"docker"`:

```python
import functools
import os
import shutil
from pathlib import Path

@functools.lru_cache(maxsize=1)
def docker_binary() -> str:
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
            return candidate
    return "docker"
```

## 2. `docker` itself can't find its credential helper

**Symptom:** the fix above resolves `docker`'s own path, but `docker` commands still fail (often on the first
pull/build of a session), because Docker Desktop's default `~/.docker/config.json` sets
`"credsStore": "desktop"`, meaning `docker` shells out to `docker-credential-desktop` by bare name for
registry auth checks - even on anonymous pulls. That helper lives right next to `docker` itself
(`~/.docker/bin/`), and `docker`'s own child-process lookup uses *your process's* PATH, not just the fact that
you found `docker`'s own path.

**Fix:** when you resolve `docker` via a fallback path (not `shutil.which`, which already implies it's on
PATH), also prepend that binary's directory to your process's own `PATH` environment variable, so every
subsequent subprocess call - including ones `docker` itself makes internally - inherits it:

```python
            bin_dir = str(path_obj.parent)
            path_parts = os.environ.get("PATH", "").split(os.pathsep)
            if bin_dir not in path_parts:
                os.environ["PATH"] = os.pathsep.join([bin_dir, *path_parts])
            return candidate
```

## 3. The Docker Desktop daemon "wedge"

**Symptom:** intermittently, some docker operations - `docker inspect`, `docker compose logs`, starting or
recreating a container, `docker compose ps` in some cases - hang indefinitely (tested well past several
minutes with no resolution), while *other* operations on the same daemon stay fast (`docker context show`,
`docker compose config --services`, sometimes `docker ps -a`). This is not your code's fault and not
predictable from anything in your compose file.

**What does NOT fix it, despite looking like it should:** waiting longer, retrying, restarting your own
process, `docker context use default`, a graceful "Quit Docker Desktop" from the menu bar (this hangs the same
way the daemon itself does).

**What has reliably fixed it, every time, all session:** the user force-quitting Docker Desktop via Activity
Monitor (not a graceful quit) and reopening it. This needs a human at the machine - **an agent should never
attempt to fix this itself.** Its job is to detect the pattern (a fast operation succeeding while a related
one times out) and say so clearly, e.g.: "docker inspect timed out after 15s - the Docker daemon may be
unresponsive; this has happened before on this machine and a manual Docker Desktop restart has always cleared
it."

**Practical corollary:** give every docker subprocess call an explicit timeout and a clear message on
timeout - never let a caller (human or agent) sit on an indefinite hang with no signal:

```python
    try:
        r = subprocess.run([db, "inspect", name, "--format", "{{json .State}}"],
                            capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "docker inspect timed out after 15s - the Docker daemon may be unresponsive."}
```

## 4. `docker compose`'s stdout buffering makes a running build look frozen

**Symptom:** you kick off `docker compose up -d --build` in the background, piping its output to a log file so
you can poll it, and the log file appears to stop updating for a long time - even though real progress is
happening underneath.

**Why:** `docker compose` block-buffers its own stdout when it isn't attached to a real TTY (i.e., when piped
to a file), so output arrives in bursts rather than a steady stream.

**Fix:** don't treat a stalled log file as a stalled operation. Cross-check against the real container state
(`docker ps -a`, or your own `/ops/status`-style endpoint) instead - if containers are actually transitioning
state, the operation is progressing even if the log looks quiet.

## 5. A shared library "not found" in a container whose image just built cleanly

**Symptom:** a Dockerfile has logic like `if [ -f /usr/lib/libssl.so.1.1 ]; then ln -sf ... ; fi` to create a
compatibility symlink, the image builds without error, but the binary that needs that symlink fails at
*runtime* with `error while loading shared libraries: ... cannot open shared object file`.

**Why:** Debian (and derivatives) install real library files under a multiarch triplet directory -
`/usr/lib/aarch64-linux-gnu/libssl.so.1.1` on arm64, `/usr/lib/x86_64-linux-gnu/...` on amd64 - not bare
`/usr/lib/`. A conditional checking the bare path silently never fires; the build "succeeds" because the `if`
just evaluates false and the `RUN` step still exits 0.

**Fix:** don't hardcode the path (you'd just be trading one wrong constant for another, and it breaks again on
a different architecture). Search for the real file at build time and symlink alongside wherever it's
actually found:

```dockerfile
RUN mkdir -p /usr/lib && \
    if ! ldconfig -p | grep -q 'libasteriskssl\.so\.1 '; then \
        SSL_LIB="$(find /usr/lib /lib -iname 'libssl.so.1.1' 2>/dev/null | head -n1)"; \
        if [ -n "$SSL_LIB" ]; then \
            ln -sf "$SSL_LIB" "$(dirname "$SSL_LIB")/libasteriskssl.so.1"; \
        fi; \
    fi && \
    ldconfig
```

If you're debugging this live rather than writing the Dockerfile fresh, a `docker run --rm --entrypoint find
<image> /usr/lib /usr/lib64 -iname '<pattern>'` against the already-built image tells you exactly where the
real file lives, without needing a shell in the container.
