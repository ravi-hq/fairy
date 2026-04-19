---
name: sprites
description: Use when working with Sprites (sprites.dev) — creating, managing, or executing commands on Linux microVMs via the sprites-py SDK or REST API. Covers the wrapper-script pattern, streaming exec, filesystem API, checkpoints, network policies, and the critical `env=` gotcha that breaks PATH. Trigger on edits to `sprites_exec.py`, `stream.py`, `runtimes.py`, adding new runtimes, or debugging Sprite connectivity/streaming/lifecycle issues.
---

# Sprites SDK Skill

Reference for working with Sprites (sprites.dev) — persistent, hardware-isolated Linux microVMs by Fly.io — in the Agent on Demand project.

## When This Skill Applies

Use this skill when:
- Writing code that creates, manages, or executes commands on Sprites
- Adding new runtimes or modifying the agent execution pipeline
- Debugging Sprite-related issues (connectivity, streaming, lifecycle)
- Working with the sprites-py Python SDK
- Modifying `sprites_exec.py`, `stream.py`, `views.py`, or `runtimes.py`

## Quick Reference

### sprites-py Installation

```
pip install sprites-py  # requires Python 3.9+
# Dependencies: httpx>=0.25.0, websockets>=12.0
```

### Client Setup

```python
from sprites import SpritesClient

client = SpritesClient(
    token="sprites_token_...",           # from Fly.io — env: SPRITES_TOKEN
    base_url="https://api.sprites.dev",  # default
    timeout=30.0,                        # HTTP timeout
    control_mode=False,                  # leave False — multiplexed WS is unreliable
)
```

### Sprite CRUD

```python
# Create
sprite = client.create_sprite("my-sprite")  # 1-2s cold, ~100ms warm

# Get handle without creating
sprite = client.sprite("existing-name")

# List (paginated)
sprite_list = client.list_sprites()  # .sprites, .continuation_token
all_sprites = client.list_all_sprites(prefix="aod-")

# Delete
client.delete_sprite("my-sprite")
```

### Command Execution

```python
# Simple capture
output = sprite.command("echo", "hello").output()  # -> bytes

# With env vars and working directory
cmd = sprite.command(
    "bash", "-c", "echo $MY_VAR",
    env={"MY_VAR": "value"},   # WARNING: replaces entire env (see gotchas)
    cwd="/home/sprite",
    timeout=600.0,
)
result = cmd.output()

# Live streaming (assign BinaryIO before .run())
cmd = sprite.command("bash", "/run-agent.sh", timeout=600)
cmd.stdout = my_writer  # any object with .write(bytes) -> int
cmd.stderr = my_writer
cmd.run()               # blocks until done, raises ExecError on non-zero

# Combined output
both = sprite.command("ls", "-la").combined_output()  # stdout + stderr
```

### Filesystem (pathlib-like API)

```python
fs = sprite.filesystem()           # working_dir defaults to "/"
path = fs / "home" / "sprite" / "file.txt"

# Write/read
path.write_text("content")
path.write_bytes(b"binary content")
text = path.read_text()
data = path.read_bytes()

# Check existence
path.exists()
path.is_file()
path.is_dir()

# Directory operations
(fs / "mydir").mkdir(parents=True, exist_ok=True)
list(path.parent.iterdir())  # yields SpritePath objects

# Delete
path.unlink()          # file
path.rmdir()           # empty dir
path.rmtree()          # recursive

# Other
path.stat()            # -> FileStat (size, mode, mtime)
path.chmod(0o755)
path.rename(fs / "new-name.txt")
path.copy_to(fs / "copy.txt")
```

### Checkpoints

```python
# Create (streams progress)
for msg in sprite.create_checkpoint(comment="pre-agent"):
    print(msg.type, msg.data)  # StreamMessage

# List
checkpoints = sprite.list_checkpoints()  # -> list[Checkpoint]

# Restore (streams progress)
for msg in sprite.restore_checkpoint(checkpoint_id):
    print(msg.type, msg.data)
```

### Sessions

```python
sessions = sprite.list_sessions()  # -> list[Session]
# Session: id, command, workdir, bytes_per_second, created_at, last_activity_at

# Reattach to running session (replays scrollback)
cmd = sprite.attach_session(session_id)
cmd.stdout = sys.stdout.buffer
cmd.run()
```

### Services (background processes that survive hibernation)

```python
from sprites import ServiceRequest

# Services auto-restart when Sprite wakes
services = sprite.list_services()      # -> list[ServiceWithState]
svc = sprite.get_service("my-svc")
sprite.delete_service("my-svc")
```

### Network Policies

```python
from sprites import NetworkPolicy, PolicyRule

# Lock down to specific domains
sprite.update_network_policy(NetworkPolicy(rules=[
    PolicyRule(domain="api.anthropic.com", action="allow"),
    PolicyRule(domain="pypi.org", action="allow"),
    PolicyRule(domain="*", action="deny"),
]))

policy = sprite.get_network_policy()
```

### URL Settings

```python
from sprites import URLSettings

# Make Sprite's HTTP endpoint public (default requires bearer token)
sprite.update_url_settings(URLSettings(auth="public"))
```

### Error Handling

```python
from sprites import (
    SpriteError,         # base
    NetworkError,        # connection failures
    AuthenticationError, # 401
    NotFoundError,       # 404
    ExecError,           # non-zero exit — has .exit_code, .stdout, .stderr
    FilesystemError,     # filesystem ops
)

try:
    cmd.run()
except ExecError as e:
    print(f"Exit code: {e.exit_code}")
    print(f"Stderr: {e.stderr}")
```

## Critical Gotchas

### 1. `env=` REPLACES the entire environment

Passing `env={"KEY": "val"}` to `sprite.command()` replaces ALL env vars — no PATH, no HOME. The binary won't be found.

**Always use the wrapper script pattern instead:**

```python
# WRONG — binary not found
sprite.command("claude", "--print", "hello",
               env={"ANTHROPIC_API_KEY": key}).run()

# RIGHT — write a wrapper script
fs = sprite.filesystem()
script = f"""#!/bin/bash
set -euo pipefail
export ANTHROPIC_API_KEY={shlex.quote(key)}
exec claude --print -p "hello"
"""
(fs / "run.sh").write_text(script)
sprite.command("chmod", "+x", "/run.sh").run()
sprite.command("bash", "/run.sh").run()
```

### 2. API keys leak in WebSocket URL query params

The exec API encodes `env=` as query params on the WS URL. Keys appear in server logs.

**The wrapper script pattern also solves this** — keys are written to the filesystem, never in the URL.

### 3. Exec does NOT invoke a shell

Shell syntax like `&&`, `|`, `>` won't work. Always wrap in `bash -c "..."`:

```python
# WRONG
sprite.command("cd", "/tmp", "&&", "ls").run()

# RIGHT
sprite.command("bash", "-c", "cd /tmp && ls").run()
```

### 4. Services --env flag is broken

Known bug. Write env vars to a file and source them in the service command instead.

### 5. RAM does NOT persist across hibernation

Running processes die when a Sprite hibernates. Only the filesystem persists. Use the Services API for processes that should auto-restart on wake.

### 6. control_mode is unreliable

Leave `control_mode=False` (default). The multiplexed WebSocket pool was disabled by default in Feb 2026. Use per-exec WebSockets.

## Fairy Project Patterns

### The Wrapper Script Pattern (sprites_exec.py)

This is the canonical way to run agents in Agent on Demand. See `src/agent_on_demand/sprites_exec.py`:

```python
def build_wrapper_script(config: RuntimeConfig, api_key: str, prompt: str) -> str:
    return f"""#!/bin/bash
set -euo pipefail
export {config.env_var}={shlex.quote(api_key)}
export PROMPT={shlex.quote(prompt)}
cd /home/sprite
mkdir -p .gemini
if [ ! -d .git ]; then
    git init -q
    git add -A 2>/dev/null || true
    git commit -q -m "init" --allow-empty 2>/dev/null || true
fi
exec {config.cmd}
"""
```

### The QueueWriter Streaming Pattern (stream.py)

Real-time streaming from Sprite → SSE response. See `src/agent_on_demand/stream.py`:

```python
class QueueWriter(io.RawIOBase):
    def __init__(self, q: queue.Queue):
        self._queue = q
    def write(self, b: bytes | bytearray) -> int:
        self._queue.put(bytes(b))
        return len(b)

# In a background thread:
cmd = sprite.command("bash", "/run-agent.sh", timeout=timeout)
cmd.stdout = QueueWriter(output_q)
cmd.stderr = QueueWriter(output_q)
cmd.run()  # blocks until agent exits
```

### Runtime Configs (runtimes.py)

```python
RUNTIMES = {
    "claude": RuntimeConfig(
        name="claude",
        cmd='claude --print --verbose --output-format stream-json -p "$PROMPT"',
        env_var="ANTHROPIC_API_KEY",
    ),
    "codex": RuntimeConfig(
        name="codex",
        cmd='echo "$PROMPT" | codex exec --full-auto --json',
        env_var="CODEX_API_KEY",
    ),
    "gemini": RuntimeConfig(
        name="gemini",
        cmd='gemini --output-format stream-json -p "$PROMPT"',
        env_var="GEMINI_API_KEY",
    ),
}
```

### Full Request Flow (views.py)

1. `POST /run` with `{runtime, prompt, api_key, timeout}`
2. Create sprite: `client.create_sprite(f"{prefix}-{uuid}")`
3. Write wrapper script via filesystem API
4. `chmod +x` the script
5. Stream output via `StreamingHttpResponse` (SSE)
6. Delete sprite in `finally` block

### Settings (config/settings.py)

| Setting | Env Var | Default |
|---------|---------|---------|
| `SPRITES_TOKEN` | `SPRITES_TOKEN` | `""` |
| `SPRITES_BASE_URL` | `SPRITES_BASE_URL` | `https://api.sprites.dev` |
| `SPRITE_NAME_PREFIX` | `SPRITE_NAME_PREFIX` | `aod` |
| `DEFAULT_TIMEOUT` | `DEFAULT_TIMEOUT` | `600` |

## Adding a New Runtime

1. Add entry to `RUNTIMES` dict in `src/agent_on_demand/runtimes.py`:
   ```python
   "newruntime": RuntimeConfig(
       name="newruntime",
       cmd='newcli --some-flags -p "$PROMPT"',
       env_var="NEW_RUNTIME_API_KEY",
   ),
   ```
2. The wrapper script (`sprites_exec.py`) handles the rest automatically
3. Update the `UserRuntimeKey.RUNTIME_CHOICES` (auto-derived from `RUNTIMES`)
4. Run `python manage.py makemigrations` if the choices affect the DB

## Sprite Lifecycle & Costs

| State | Wake Time | Cost |
|-------|-----------|------|
| Creating | 1-2s | Compute charges start |
| Active | — | ~$0.07/CPU-hr + $0.04375/GB-hr |
| Warm (just idle) | 100-500ms | **$0** compute |
| Cold (long idle) | 1-12s | **$0** compute, small storage |

A typical 5-minute agent run costs ~$0.01-0.02.

## Pre-installed on Every Sprite

- **AI CLIs**: Claude Code, Codex, Gemini CLI
- **Languages**: Node.js 22, Python 3.13, Go, Ruby, Rust, Elixir, Java, Bun, Deno
- **Tools**: git, curl, vim, etc.
- **Storage**: 100GB persistent NVMe (ext4)
- **Resources**: Up to 8 CPUs, 2-16GB RAM (autoscale)

## CLI Quick Reference (for debugging)

```bash
sprite create my-sprite       # Create
sprite exec echo hello        # Run command
sprite console                # Interactive shell
sprite list                   # List sprites
sprite destroy -s my-sprite   # Delete
sprite url                    # Get public URL
sprite url update --auth public  # Make public
sprite checkpoint create      # Snapshot state
sprite proxy 8080             # Forward local port
```

Auth: `sprite org auth` (uses Fly.io account).

## REST API Endpoints

Base: `https://api.sprites.dev/v1`
Auth: `Authorization: Bearer $SPRITES_TOKEN`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/sprites` | Create sprite |
| GET | `/sprites` | List (paginated) |
| GET | `/sprites/{name}` | Get details |
| DELETE | `/sprites/{name}` | Destroy |
| WSS | `/sprites/{name}/exec?cmd=...` | Streaming exec (binary WS protocol) |
| POST | `/sprites/{name}/exec?cmd=...` | Blocking exec |
| GET | `/sprites/{name}/filesystem/{path}` | Read file |
| POST | `/sprites/{name}/filesystem/{path}` | Write file |
| POST | `/sprites/{name}/checkpoint` | Create checkpoint |
| POST | `/sprites/{name}/checkpoints/{id}/restore` | Restore |
| GET/POST | `/sprites/{name}/policies/network` | Network policies |
| POST | `/sprites/{name}/services` | Create service |

## WebSocket Binary Protocol (exec)

Each message: `[Stream ID: 1 byte][Payload: N bytes]`

| Stream ID | Direction | Meaning |
|-----------|-----------|---------|
| 0 | Client->Server | stdin |
| 1 | Server->Client | stdout |
| 2 | Server->Client | stderr |
| 3 | Server->Client | exit code |
| 4 | Client->Server | stdin EOF |
