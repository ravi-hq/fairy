---
date: 2026-04-16T10:00:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 7ca7e0ed13ec6deab60e99ee16f5368e95798719
branch: main
repository: ravi-hq/fairy
topic: "Sprites platform deep dive — SDK surface, API patterns, and fairy integration"
tags: [research, team-research, sprites, sprites-py, fairy, ai-agents]
status: complete
method: agent-team
team_size: 1
tracks: [docs-sdk-fairy-synthesis]
last_updated: 2026-04-16
last_updated_by: Claude Code
---

# Research: Sprites Deep Dive — Full SDK Surface for Skill Synthesis

**Date**: 2026-04-16
**Researcher**: Claude Code (team-research)
**Git Commit**: [`7ca7e0e`](https://github.com/ravi-hq/fairy/commit/7ca7e0ed13ec6deab60e99ee16f5368e95798719)
**Branch**: `main`
**Repository**: ravi-hq/fairy

## Research Question

Comprehensive catalog of everything Sprites makes available — CLI, REST API, Python SDK, filesystem, checkpoints, services, network policies — to synthesize a Claude Code skill for working with Sprites in the fairy project.

## Summary

This document extends the 2026-04-15 research with specific SDK class/method signatures, fairy project integration patterns, and the full type surface needed to write a working skill. The fairy project already uses sprites-py correctly (wrapper script pattern, QueueWriter streaming, SSE responses). The skill should encode these patterns plus the gotchas discovered.

## 1. sprites-py SDK — Complete Public API

### Exports (`sprites/__init__.py`)

**Classes**: `SpritesClient`, `Sprite`, `SpriteFilesystem`, `SpritePath`, `ControlConnection`, `OpConn`

**Exceptions**: `SpriteError`, `NetworkError`, `AuthenticationError`, `NotFoundError`, `ExecError`, `FilesystemError`, `FileNotFoundError_`, `IsADirectoryError_`, `NotADirectoryError_`, `PermissionError_`, `DirectoryNotEmptyError`

**Types**: `ClientOptions`, `URLSettings`, `SpriteConfig`, `SpawnOptions`, `ExecOptions`, `ExecResult`, `SpriteInfo`, `ListOptions`, `SpriteList`, `Session`, `Checkpoint`, `StreamMessage`, `PortMapping`, `Service`, `ServiceState`, `ServiceWithState`, `ServiceRequest`, `ServiceLogEvent`, `PolicyRule`, `NetworkPolicy`, `FileStat`, `DirEntry`

### SpritesClient Methods

| Method | Signature | Returns | Notes |
|--------|-----------|---------|-------|
| `__init__` | `(token, base_url="https://api.sprites.dev", timeout=30.0, control_mode=False)` | — | control_mode enables multiplexed WS pool |
| `close` | `()` | None | Closes httpx client |
| `sprite` | `(name)` | `Sprite` | Local handle, no API call |
| `create_sprite` | `(name, config=None)` | `Sprite` | POST /v1/sprites |
| `get_sprite` | `(name)` | `Sprite` | GET /v1/sprites/{name} |
| `list_sprites` | `(options=None)` | `SpriteList` | Paginated |
| `list_all_sprites` | `(prefix=None)` | `list[Sprite]` | Auto-paginated |
| `delete_sprite` | `(name)` | None | DELETE /v1/sprites/{name} |
| `upgrade_sprite` | `(name)` | None | POST /v1/sprites/{name}/upgrade |
| `update_url_settings` | `(name, settings)` | None | PUT /v1/sprites/{name} |
| `create_token` | `(fly_macaroon, org_slug, invite_code)` | str | Static method |

### Sprite Methods

| Category | Method | Signature | Returns |
|----------|--------|-----------|---------|
| **Exec** | `command` | `(*args, env=None, cwd=None, timeout=None)` | `Cmd` |
| **Exec** | `attach_session` | `(session_id, timeout=None)` | `Cmd` |
| **Lifecycle** | `delete` / `destroy` | `()` | None |
| **Lifecycle** | `upgrade` | `()` | None |
| **URL** | `update_url_settings` | `(settings)` | None |
| **Sessions** | `list_sessions` | `()` | `list[Session]` |
| **Filesystem** | `filesystem` | `(working_dir="/")` | `SpriteFilesystem` |
| **Checkpoints** | `list_checkpoints` | `(history_filter=None)` | `list[Checkpoint]` |
| **Checkpoints** | `get_checkpoint` | `(checkpoint_id)` | `Checkpoint` |
| **Checkpoints** | `create_checkpoint` | `(comment=None)` | Generator[StreamMessage] |
| **Checkpoints** | `restore_checkpoint` | `(checkpoint_id)` | Generator[StreamMessage] |
| **Services** | `list_services` | `()` | `list[ServiceWithState]` |
| **Services** | `get_service` | `(name)` | `ServiceWithState` |
| **Services** | `delete_service` | `(name)` | None |
| **Network** | `get_network_policy` | `()` | `NetworkPolicy` |
| **Network** | `update_network_policy` | `(policy)` | None |

### Cmd Class

| Field/Method | Type/Signature | Notes |
|-------------|----------------|-------|
| `stdin` | `BinaryIO` | Set before run() for input |
| `stdout` | `BinaryIO` | Set before run() for live streaming |
| `stderr` | `BinaryIO` | Set before run() for live streaming |
| `run()` | `() -> None` | Blocks, raises ExecError on non-zero |
| `output()` | `() -> bytes` | Captures stdout only |
| `combined_output()` | `() -> bytes` | Captures stdout+stderr |

### SpriteFilesystem / SpritePath

- `fs = sprite.filesystem(working_dir="/")` — returns `SpriteFilesystem`
- `path = fs / "dir" / "file.txt"` — returns `SpritePath`
- SpritePath implements: `read_text()`, `read_bytes()`, `write_text()`, `write_bytes()`, `exists()`, `is_file()`, `is_dir()`, `stat()`, `mkdir()`, `unlink()`, `rmdir()`, `rmtree()`, `chmod()`, `rename()`, `copy_to()`, `touch()`, `iterdir()`, `listdir()`
- API endpoints: `/fs/read`, `/fs/write`, `/fs/list`, `/fs/delete`, `/fs/rename`, `/fs/copy`, `/fs/chmod`

## 2. Fairy Project — Current Implementation

### Architecture

Django app at `src/fairy/` with:
- **`views.py`**: `POST /run` endpoint — creates Sprite, writes wrapper script, streams SSE events
- **`runtimes.py`**: `RuntimeConfig` dataclass with `name`, `cmd`, `env_var` per runtime
- **`sprites_exec.py`**: `build_wrapper_script()` — generates bash script with env export + agent exec
- **`stream.py`**: `stream_agent_output()` — QueueWriter pattern for real-time streaming via threads
- **`models.py`**: `APIKey` (sha256 hashed), `UserRuntimeKey` (Fernet encrypted)
- **`crypto.py`**: Fernet encryption derived from Django SECRET_KEY
- **`admin.py`**: Full Django admin with inline API key + runtime key management

### Supported Runtimes

| Runtime | Command | Env Var |
|---------|---------|---------|
| claude | `claude --print --verbose --output-format stream-json -p "$PROMPT"` | `ANTHROPIC_API_KEY` |
| codex | `echo "$PROMPT" \| codex exec --full-auto --json` | `CODEX_API_KEY` |
| gemini | `gemini --output-format stream-json -p "$PROMPT"` | `GEMINI_API_KEY` |

### Settings (env vars)

- `SPRITES_TOKEN` — API token for Sprites
- `SPRITES_BASE_URL` — defaults to `https://api.sprites.dev`
- `SPRITE_NAME_PREFIX` — defaults to `fairy`
- `DEFAULT_TIMEOUT` — defaults to 600

## 3. Key Patterns Used by Fairy

### Wrapper Script Pattern (from sprites_exec.py)
```python
def build_wrapper_script(config, api_key, prompt):
    return f"""#!/bin/bash
set -euo pipefail
export {config.env_var}={shlex.quote(api_key)}
export PROMPT={shlex.quote(prompt)}
cd /home/sprite
mkdir -p .gemini
if [ ! -d .git ]; then
    git init -q && git add -A && git commit -q -m "init" --allow-empty
fi
exec {config.cmd}
"""
```

### QueueWriter Streaming Pattern (from stream.py)
```python
class QueueWriter(io.RawIOBase):
    def __init__(self, q): self._queue = q
    def write(self, b): self._queue.put(bytes(b)); return len(b)

cmd = sprite.command("bash", "/run-agent.sh", timeout=timeout)
cmd.stdout = QueueWriter(output_q)
cmd.stderr = QueueWriter(output_q)
cmd.run()  # blocks in background thread
```

### Filesystem Write Pattern (from views.py)
```python
fs = sprite.filesystem()
(fs / "run-agent.sh").write_text(script)
sprite.command("chmod", "+x", "/run-agent.sh").run()
```

## Related Research

- `thoughts/research/2026-04-15-sprites-platform-research.md` — Full platform overview, pricing, gotchas, architecture recommendations
- `thoughts/plans/2026-04-15-fairy-api.md` — Original fairy API implementation plan

## Open Questions

1. **Sprite reuse/pooling** — Currently creates + destroys per request. Pool pattern would eliminate 1-2s create latency.
2. **Codex CLI flag** — `codex exec --full-auto --json` is the current command; verify this is still the correct invocation.
3. **Network policy lockdown** — Should fairy enforce network policies on created Sprites to prevent exfiltration?
4. **Auth for /run endpoint** — Currently no auth middleware. The `APIKey` model exists but isn't wired to views yet.
