---
date: 2026-04-15T19:30:00-07:00
researcher: Claude Code (team-research skill)
git_commit: n/a (no git repo)
branch: n/a
repository: local
topic: "Sprites platform capabilities for AI agent orchestration API"
tags: [research, team-research, sprites, ai-agents, infrastructure, streaming]
status: complete
method: agent-team
team_size: 4
tracks: [docs-api, python-sdk, ai-patterns, ops-limits]
last_updated: 2026-04-15
last_updated_by: Claude Code
---

# Research: Sprites Platform — Full API Surface & AI Agent Orchestration

**Date**: 2026-04-15
**Researcher**: Claude Code (team-research, 4 specialist researchers)
**Repository**: ravi-hq/fairy (local)
**Method**: Agent team — docs crawler, SDK analyst, AI patterns researcher, ops researcher

## Research Question

What does https://sprites.dev make available, and how can we build an API on top of it that takes `runtime + prompt + API keys`, triggers Claude Code / Gemini / Codex, and streams back events?

## Summary

Sprites (by Fly.io) provides persistent, hardware-isolated Linux microVMs with **all three target AI CLIs pre-installed** (Claude Code, Gemini CLI, Codex CLI). The platform exposes a comprehensive REST + WebSocket API with a well-designed Python SDK (`sprites-py`). The critical capability for our use case is the **WebSocket exec endpoint** which streams real-time stdout/stderr from commands running inside a Sprite using a simple binary protocol. Environment variables (API keys) can be passed per-exec call. The platform bills per-second with zero cost when idle, making it economically viable for on-demand agent execution.

---

## 1. Platform Overview

Sprites are Firecracker microVMs running Ubuntu 25.10 with:
- **8 CPUs, 2-16GB RAM** (autoscales), **100GB persistent NVMe storage**
- Full ext4 filesystem that persists between runs
- Wake from hibernation in 100-500ms
- Per-second billing, zero cost when idle
- Hardware-level isolation (not container-level)

### Pre-installed Runtimes & AI CLIs

Every Sprite comes with:

| Category | Tools |
|----------|-------|
| **AI CLIs** | Claude Code (`@anthropic-ai/claude-code`), OpenAI Codex (`@openai/codex`), Gemini CLI (`@google/gemini-cli`) |
| **Languages** | Node.js 22.20, Python 3.13, Go, Ruby, Rust, Elixir, Java, Bun, Deno |
| **Tools** | git, curl, vim, and 10+ others |

**Key insight**: No installation step needed for our target CLIs. Create a Sprite and run immediately.

---

## 2. REST API

**Base URL**: `https://api.sprites.dev/v1`
**Auth**: `Authorization: Bearer $SPRITES_TOKEN`
**Tokens**: Created at `sprites.dev/account/{org}/tokens`

### Sprite CRUD

| Method | Path | Description |
|--------|------|-------------|
| POST | `/sprites` | Create sprite (SpriteConfig: `ram_mb`, `cpus`, `region`, `storage_gb`) |
| GET | `/sprites` | List sprites (paginated: `max_results`, `continuation_token`, `prefix`) |
| GET | `/sprites/{name}` | Get sprite details |
| PUT | `/sprites/{name}` | Update sprite (URL settings) |
| DELETE | `/sprites/{name}` | Destroy sprite |
| POST | `/sprites/{name}/upgrade` | Upgrade sprite |

### Command Execution (Critical Path)

| Method | Path | Description |
|--------|------|-------------|
| **WSS** | `/sprites/{name}/exec?cmd=...&env=...` | **Streaming exec** — binary WebSocket protocol |
| POST | `/sprites/{name}/exec?cmd=...&env=...` | Non-streaming exec — blocks until completion |
| WSS | `/sprites/{name}/exec/{session_id}` | Reattach to existing session (replays scrollback) |

**Query parameters for exec:**
- `cmd` (repeatable) — command and arguments
- `env` (repeatable) — `KEY=VALUE` format, replaces default environment
- `stdin` — whether to accept stdin

### WebSocket Binary Protocol (non-TTY mode)

Each binary WebSocket message:
```
[Stream ID: 1 byte] [Payload: N bytes]
```

| Stream ID | Direction | Meaning |
|-----------|-----------|---------|
| 0 | Client→Server | stdin |
| 1 | Server→Client | stdout |
| 2 | Server→Client | stderr |
| 3 | Server→Client | exit code |
| 4 | Client→Server | stdin EOF |

**JSON control messages from server:**
- `session_info` — session metadata on connect
- `exit` — process terminated, includes exit code
- `port_opened` / `port_closed` — port lifecycle events

**Session persistence**: `max_run_after_disconnect` controls how long a non-TTY session survives without a client (default: 10s).

### Checkpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/sprites/{name}/checkpoint` | Create checkpoint (~300ms, streams NDJSON progress) |
| GET | `/sprites/{name}/checkpoints` | List checkpoints (id, create_time, comment, health) |
| POST | `/sprites/{name}/checkpoints/{id}/restore` | Restore checkpoint (streams NDJSON progress) |

Last 5 checkpoints mounted at `/.sprite/checkpoints` inside the Sprite.

### Filesystem

| Method | Path | Description |
|--------|------|-------------|
| GET | `/sprites/{name}/filesystem/{path}` | Read file/directory |
| POST | `/sprites/{name}/filesystem/{path}` | Write file |
| DELETE | `/sprites/{name}/filesystem/{path}` | Delete file |
| WSS | `/sprites/{name}/filesystem/watch` | Real-time filesystem change stream |
| — | `.../copy`, `.../rename`, `.../chmod`, `.../chown` | File operations |

### Services (background processes)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/sprites/{name}/services` | Create service (cmd, args, needs, http_port) |
| GET | `/sprites/{name}/services` | List services |
| GET/DELETE | `/sprites/{name}/services/{id}` | Get/delete service |
| POST | `.../start`, `.../stop`, `.../restart` | Lifecycle |
| GET | `.../logs` | Service logs |

Services auto-restart on Sprite wake. Useful for persistent agent processes.

### Network Policies

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/sprites/{name}/policies/network` | DNS domain whitelisting (allow/deny rules) |
| GET/POST/DELETE | `/sprites/{name}/policies/privileges` | Privilege policies |
| GET/POST/DELETE | `/sprites/{name}/policies/resources` | Resource policies |

### Proxy

| Method | Path | Description |
|--------|------|-------------|
| WSS | `/sprites/{name}/proxy` | TCP tunnel to Sprite |

### Other

- Every Sprite gets a unique public URL for HTTP access
- URL auth configurable: `public` or `sprite`
- MCP endpoint at `https://sprites.dev/mcp` (OAuth-based, but **cannot stream** — not suitable for our use case)

---

## 3. Python SDK (`sprites-py`)

**Install**: `pip install sprites-py`
**Version**: 0.1.0
**Dependencies**: `httpx>=0.25.0`, `websockets>=12.0`
**Architecture**: Synchronous surface API, async internals (background event loop in daemon thread)

### SpritesClient

```python
from sprites import SpritesClient

client = SpritesClient(
    token="sprites_token_...",
    base_url="https://api.sprites.dev",  # default
    timeout=30.0,
    control_mode=False,  # True enables multiplexed WebSocket pool (up to 100 conns)
)
```

| Method | Returns | REST Mapping |
|--------|---------|-------------|
| `client.sprite(name)` | `Sprite` | No API call — local handle |
| `client.create_sprite(name, config?)` | `Sprite` | POST /v1/sprites |
| `client.get_sprite(name)` | `Sprite` | GET /v1/sprites/{name} |
| `client.list_sprites(options?)` | `SpriteList` | GET /v1/sprites |
| `client.list_all_sprites(prefix?)` | `list[Sprite]` | Auto-paginated |
| `client.delete_sprite(name)` | `None` | DELETE /v1/sprites/{name} |
| `client.upgrade_sprite(name)` | `None` | POST /v1/sprites/{name}/upgrade |

### Command Execution — The Critical API

**Go-style API (recommended):**
```python
sprite = client.create_sprite("agent-run-123")

# Simple capture
output = sprite.command("claude", "--print", "write hello world").output()

# Live streaming to caller
cmd = sprite.command(
    "claude", "--print", prompt,
    env={"ANTHROPIC_API_KEY": api_key},
    cwd="/workspace",
    timeout=3600.0,
)
cmd.stdout = my_output_stream  # any BinaryIO — stream in real-time
cmd.stderr = my_error_stream
cmd.run()  # blocks until complete, raises ExitError on non-zero
```

**Subprocess-style API:**
```python
from sprites.exec import run

result = run(
    sprite, "claude", "--print", prompt,
    capture_output=True,
    timeout=300.0,
    check=True,
    env={"ANTHROPIC_API_KEY": api_key},
    cwd="/workspace",
)
# result.returncode, result.stdout (bytes), result.stderr (bytes)
```

**Cmd class full interface:**
```python
cmd = Cmd(
    sprite, args=["claude", "--print", prompt],
    env={"ANTHROPIC_API_KEY": "..."},
    cwd="/workspace",
    stdin=None,       # BinaryIO
    stdout=None,      # BinaryIO — set for live streaming
    stderr=None,      # BinaryIO
    tty=False,        # True for interactive/detachable sessions
    tty_rows=24, tty_cols=80,
    session_id=None,  # attach to existing session
    timeout=None,
)

cmd.run()             # execute, raise on error
cmd.output()          # -> bytes (captured stdout)
cmd.combined_output() # -> bytes (stdout + stderr)
```

### Session Management (detach/reattach)

```python
# List active sessions
sessions = sprite.list_sessions()
# Session: id, command, workdir, bytes_per_second, created_at, last_activity_at

# Reattach to running session — replays buffered output + live stream
cmd = sprite.attach_session(session_id)
cmd.stdout = sys.stdout.buffer
cmd.run()

# Kill session
sprite.kill_session(session_id, signal="SIGTERM", timeout=10)
```

### Filesystem (pathlib-compatible)

```python
fs = sprite.filesystem()
path = fs / "workspace" / "file.txt"

path.write_text("hello")
path.read_text()
path.exists()
path.mkdir(parents=True, exist_ok=True)
path.unlink()
path.stat()  # -> FileStat
list(path.iterdir())
```

### Checkpoints

```python
# Create (streaming progress)
for msg in sprite.create_checkpoint(comment="pre-agent"):
    print(msg)  # StreamMessage: type, data, error

# List
checkpoints = sprite.list_checkpoints()  # -> list[Checkpoint]

# Restore
for msg in sprite.restore_checkpoint(checkpoint_id):
    print(msg)
```

### Network Policy

```python
from sprites import NetworkPolicy, PolicyRule

sprite.update_network_policy(NetworkPolicy(rules=[
    PolicyRule(domain="api.anthropic.com", action="allow"),
    PolicyRule(domain="pypi.org", action="allow"),
    PolicyRule(domain="*", action="deny"),
]))
```

### Services

```python
from sprites.services import create_service, start_service, stop_service

stream = create_service(sprite, name="agent", cmd="claude", args=["--print", prompt],
                        http_port=8080, duration=30.0)
for event in stream:  # ServiceLogEvent
    print(event)
```

### Error Handling

```
SpriteError (base)
├── NetworkError
├── AuthenticationError  
├── NotFoundError
├── APIError             # .is_rate_limit(), .retry_after()
├── ExecError/ExitError  # .exit_code, .stdout, .stderr
├── TimeoutError
└── FilesystemError (+ POSIX-mapped subclasses)
```

---

## 4. AI CLI Integration Details

### Per-CLI Configuration

| CLI | Auth Env Var | Non-interactive Flag | Structured Streaming |
|-----|-------------|---------------------|---------------------|
| Claude Code | `ANTHROPIC_API_KEY` | `claude --print "prompt"` | `--output-format stream-json` for NDJSON events |
| Codex CLI | `OPENAI_API_KEY` | `codex --approval-mode auto-edit -q "prompt"` | `--json` for NDJSON events |
| Gemini CLI | `GOOGLE_API_KEY` | Headless mode supported | `--output-format stream-json` for NDJSON |

All three support:
- API key via environment variable (no OAuth needed headless)
- Non-interactive execution
- Streaming stdout (line-by-line or structured JSON events)

### Critical Gotcha

The exec endpoint does **NOT invoke a shell by default**. Shell syntax like `cd dir && cmd` will fail. Always wrap in explicit shell:
```
cmd=bash&cmd=-c&cmd=cd /project && claude --print "prompt"
```

In Python SDK:
```python
sprite.command("bash", "-c", f'cd /project && claude --print "{prompt}"',
               env={"ANTHROPIC_API_KEY": key})
```

---

## 5. Pricing & Operational Limits

### Billing

| Resource | Rate | Minimum floor |
|----------|------|---------------|
| CPU | $0.07/CPU-hour | 6.25% CPU utilization/sec |
| Memory | $0.04375/GB-hour | 0.25 GB (250 MB)/sec |
| Hot Storage (NVMe) | $0.000683/GB-hour | Billed on actual blocks written (TRIM-aware) |
| Durable Storage | $0.000027/GB-hour | — |
| **Idle** | **$0.00** | Zero compute charges |

**Cost examples:** ~$0.46 for a 4-hour session. ~$0.01-0.02 for a 5-minute agent run. $30 free trial credits on signup.

### Resources

- **CPU**: Up to 8 CPUs (burst/autoscale)
- **RAM**: Starts at 2GB, autoscales to 16GB
- **Disk**: 100GB persistent NVMe (copy-on-write, only changed blocks billed)
- **GPU**: Not supported (CPU-only) — fine for orchestrating external LLM API calls
- **Regions**: Auto-assigned by caller geography (Fly anycast, 18 global regions)

### Lifecycle States

```
create → active → idle (warm) → idle (cold) → destroy
                    ↑                ↑
                    └── wake (fast) ──┘── wake (slower)
```

- **Active**: Running commands, TCP connections, or TTY sessions
- **Warm**: Just went idle, fast resume
- **Cold**: Idle for a while, filesystem backed up to durable storage, slower wake
- Idle detection based on: active commands, TCP connections, TTY sessions, services with connections

### Idle & Timeout Behavior

- **Idle timeout**: ~30 seconds of inactivity triggers hibernation
- **Inactivity** = no HTTP requests + no active exec sessions + no active TCP connections + no running services
- **RAM does NOT persist** across hibernation — running processes are killed
- **Filesystem PERSISTS** across hibernation
- **Services** (registered via Services API) auto-restart on wake
- **TTY exec sessions terminate on sleep** — use Services mechanism for long-running agents
- **`max_run_after_disconnect`**: TTY default=0 (runs indefinitely), non-TTY default=10s (terminates). Configurable.
- **No hard execution timeout** — commands run as long as the Sprite stays active

### Cold Start Performance

| State | Wake time |
|-------|-----------|
| Warm (recently active) | 100-500ms |
| Cold (from object storage) | 1-12 seconds |
| Checkpoint restore | ~300ms |

### Network

- Every Sprite gets a unique public URL (auth: `public` or `sprite`)
- Network policies: DNS-based domain whitelisting (allow/deny), wildcards supported (`*.npmjs.org`)
- DNS REFUSED for denied domains, changes take effect immediately
- TCP proxy available via WebSocket
- Inbound HTTP routes through Fly proxy on port 8080

### Concurrency & Rate Limits

- **Warm sprite limits**: Per-org hard limit enforced by billing tier (numbers not public)
- **Concurrent active sprite limits**: Separate per-org limit (not public)
- **API rate limits**: Exist (SDK has `APIError.is_rate_limit()`, `.retry_after()`) but not publicly documented
- **List pagination**: Max 50 sprites per page with continuation tokens
- **$30 trial credits** on signup (~500 Sprites worth)

---

## 6. Critical Implementation Gotchas (Cross-Team Discoveries)

### Gotcha 1: `env` dict REPLACES the default environment (does not merge)

Passing any `env` vars via `cmd.env` (which maps to `env=KEY=VALUE` query params on the WebSocket URL) **replaces the entire default environment**. The process gets ONLY the vars you pass — no PATH, no HOME, nothing. The agent binary won't even be found.

**Mitigation options:**
1. **Wrapper script** (recommended): Write a shell script to the Sprite filesystem first, then exec it with no env override
2. **Full env dict**: Build a complete environment (PATH, HOME, USER, TERM, LANG, etc.) + API keys for every invocation
3. **Investigate Sprite default env**: Query/capture the default env to merge into

### Gotcha 2: API keys may be exposed in WebSocket URL query params

The exec API passes env vars as query parameters on the WebSocket URL (`WSS ...?env=ANTHROPIC_API_KEY=sk-ant-...`). This means keys appear in:
- Sprites' server-side access logs
- Intermediary proxy/load balancer logs
- Process listings

**Mitigation options:**
1. **Wrapper script** (recommended — solves both gotchas): Use filesystem API to write a script that `export`s the key and `exec`s the agent. Key never touches URL.
2. **Write `.env` file first**: `sprite exec bash -c 'echo ANTHROPIC_API_KEY=... >> ~/.env'` then exec agent sourcing it. Writes key to persistent disk (risk if Sprite reused).
3. **Accept the risk for MVP**: Sprites infrastructure is already trusted (we give them our API token).

### Gotcha 3: Exec endpoint does NOT invoke a shell

Shell syntax like `cd dir && cmd` fails. Always wrap in explicit shell:
```python
sprite.command("bash", "-c", f'cd /project && claude --print "{prompt}"')
```

### Gotcha 4: Services `--env` flag is broken

Known bug: `--env` on `sprite-env services create` does NOT store/pass variables. The exec endpoint `env` parameter works correctly. For services, write env vars to a file first and source them in the command.

---

## 7. Recommended Architecture for Fairy API

Based on all findings, here's the optimal architecture:

### API Contract

```
POST /run
{
  "runtime": "claude" | "codex" | "gemini",
  "prompt": "string",
  "api_key": "string",
  "options": {
    "cwd": "/workspace",
    "timeout": 300,
    "stream_format": "raw" | "ndjson"
  }
}

-> SSE stream of events back to caller
```

### Implementation Flow (with wrapper script to avoid env gotchas)

```python
from sprites import SpritesClient
import uuid

RUNTIME_CONFIG = {
    "claude": {
        "cmd": 'claude --print --output-format stream-json "$PROMPT"',
        "env_var": "ANTHROPIC_API_KEY",
    },
    "codex": {
        "cmd": 'codex --approval-mode auto-edit --json -q "$PROMPT"',
        "env_var": "OPENAI_API_KEY",
    },
    "gemini": {
        "cmd": 'gemini --output-format stream-json "$PROMPT"',
        "env_var": "GOOGLE_API_KEY",
    },
}

client = SpritesClient(token=SPRITES_TOKEN)

def run_agent(runtime: str, prompt: str, api_key: str, stream_callback):
    name = f"fairy-{uuid.uuid4().hex[:8]}"
    config = RUNTIME_CONFIG[runtime]
    
    # 1. Create sprite (~1-2s, CLIs already installed)
    sprite = client.create_sprite(name)
    
    try:
        # 2. Write wrapper script (avoids env-replace + URL-logging gotchas)
        fs = sprite.filesystem()
        script = f'''#!/bin/bash
export {config["env_var"]}="{api_key}"
export PROMPT={shlex.quote(prompt)}
cd /workspace
exec {config["cmd"]}
'''
        (fs / "run-agent.sh").write_text(script)
        sprite.command("chmod", "+x", "/run-agent.sh").run()
        
        # 3. Execute with streaming (no env= params in URL)
        cmd = sprite.command("bash", "/run-agent.sh", timeout=600)
        cmd.stdout = StreamAdapter(stream_callback)  # forwards to SSE/NDJSON
        cmd.stderr = StreamAdapter(stream_callback)
        cmd.run()
        
    finally:
        # 4. Cleanup
        client.delete_sprite(name)
```

### Optimizations

1. **Sprite pooling**: Keep warm Sprites in a pool. Assign on request, return after completion. Avoids 1-2s create overhead. Zero cost while idle.

2. **Checkpoints for pre-configured state**: If users need specific repos cloned or packages installed, checkpoint that state and restore on demand (~300ms).

3. **Session detach/reattach**: For long-running agents, use TTY mode with session IDs. Client can disconnect and reconnect without losing output (scrollback buffer replayed).

4. **Network policies**: Lock down Sprites to only the AI provider's API domain + package registries. Prevents exfiltration from untrusted code.

5. **Control mode**: Enable `control_mode=True` for concurrent multi-agent scenarios. Multiplexed WebSocket pool supports up to 100 connections. Note: default off since Feb 2026 due to reliability concerns — use per-exec WebSockets unless proven needed.

### Key Design Decisions

| Decision | Recommendation | Reason |
|----------|---------------|--------|
| Streaming mechanism | WebSocket exec, NOT MCP | MCP cannot stream; WebSocket gives real-time binary stdout/stderr |
| API key passing | Wrapper script on Sprite filesystem | Avoids env-replace gotcha AND URL query param logging exposure |
| Shell wrapping | Always use `bash -c "..."` or wrapper script | Exec endpoint doesn't invoke shell by default |
| Sprite lifecycle | Pool + cleanup | Create per-request is fine (1-2s), but pooling eliminates that latency |
| Output format | NDJSON (`--output-format stream-json`) | All three CLIs support it; parseable structured events |
| Control mode | Off (per-exec WebSockets) | Default off since Feb 2026; simpler, more reliable |

---

## 7. Available SDKs

| Language | Package | Status |
|----------|---------|--------|
| Python | `sprites-py` (pip) | Mature, well-documented |
| JavaScript/Node | `@fly/sprites` (npm) | Available |
| Go | `github.com/superfly/sprites-go` | Available |
| Elixir | `superfly/sprites-ex` | Available |

---

## 8. MCP Support (Not Recommended for Our Use Case)

Sprites has MCP at `https://sprites.dev/mcp` with OAuth auth. Exposes tools for sprite CRUD, exec, services, checkpoints, network policies. However, **MCP exec cannot stream** because MCP lacks WebSocket support. Shell operators are also treated as literal args. Fly.io themselves recommend the direct API/CLI over MCP for agent orchestration.

---

## Open Questions

1. **Sprite creation latency under load** — Is 1-2s consistent, or does it degrade at scale?
2. **Concurrent Sprite limits per org** — Not documented. Need to test or contact Fly.io.
3. **API rate limits** — SDK has `APIError.is_rate_limit()` and `.retry_after()`, but specific limits not documented.
4. **Cross-Sprite templates** — Checkpoints are per-Sprite (no "create from checkpoint"). How to efficiently pre-configure Sprites?
5. **`--output-format stream-json` consistency** — Need to verify this flag works identically across all three CLIs. Codex uses `--json` instead.
6. **Warm pool economics** — What's the storage cost of keeping N warm Sprites idle with pre-installed state?
