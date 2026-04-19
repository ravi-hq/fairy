# session_service API Refactor — Implementation Plan

## Overview

Reshape `session_service.provision_session` so the view layer stops handing it a
prebuilt wrapper script and initial prompt. After this change the view builds a
single `SessionSpec` (runtime + api_key + environment + repos + mcp + skills +
runtime_session_id), and the service internally decides *how* to set up the
Sprite — running each setup stage as its own `sprite.command()` or filesystem
write, then writing a tiny `/run-agent.sh` dispatcher.

Net effect: setup stages are independently observable (exit code + stderr per
stage), the wrapper script collapses from ~90 lines of generated shell to a
~15-line dispatcher, and API keys move from the script body into an env file
on disk.

No DB migration. No API surface change. Pure internal refactor.

## Research Summary

Backed by `thoughts/research/2026-04-18-sprites-script-setup.md`.

- **Track 1 — Script anatomy**: The current `run_agent.sh.tmpl` packs packages,
  clone, user setup_script, MCP config, skills, and the runtime CLI invocation
  into one gated script. `/tmp/aod-initialized` sentinel gates non-idempotent
  work. All errors surface as opaque stderr with a single `AOD_STAGE_FAILED`
  marker.
- **Track 2 — Django-side setup**: `_create_session` calls `build_wrapper_script`
  then hands the string to `provision_session(wrapper_script=..., prompt=...)`.
  Same assembly repeats nowhere — script is write-once. `write_prompt` is the
  only per-turn state update.
- **Track 4 — Debug surface**: The biggest documented pain is "apt failed
  silently, grep stderr in AgentSessionLog." Stage-per-command provisioning
  gives each stage its own Sprites exit code, which is what this refactor
  primarily buys.

### Key Discoveries

- `provision_session` signature to flip: `src/agent_on_demand/session_service.py:70-106`
  (currently `name, environment, wrapper_script, prompt`; target: `spec: SessionSpec`).
- `build_wrapper_script` call site: `src/agent_on_demand/views/sessions.py:261-269`
  — only one caller; will be deleted.
- `send_prompt` continue path: `src/agent_on_demand/views/sessions.py:432-437`
  — calls `write_prompt` then `start_turn`. Wraps into a `run_turn` helper.
- Dataclasses to reshape / rehome: `EnvironmentSetup` (`sprites_exec.py:11-18`),
  `RepoSpec` (`sprites_exec.py:20-24`), `McpServerSpec` (`sprites_exec.py:27-40`),
  `SkillSpec` (`sprites_exec.py:42-51`).
- Stage logic to extract from `sprites_exec.py`:
  - `_build_env_vars_section` (57-64) → `_write_env_file(sprite, spec)` (fs write)
  - `_build_packages_section` (67-97) → `_install_packages(sprite, env)` (one
    `sprite.command` per package manager)
  - `_build_clone_section` (107-130) → `_clone_repos(sprite, repos)` (credentials
    file + one `sprite.command` per repo, cleanup in finally)
  - `_build_setup_script_section` (100-104) → `_run_user_setup(sprite, env)`
    (single `sprite.command` over the user's script, piped via stdin)
  - `_build_mcp_section` / `_build_mcp_claude` / `_build_mcp_codex` /
    `_build_mcp_gemini` (133-214) → `_write_mcp_config(sprite, spec)` (fs writes,
    runtime-specific paths)
  - `_build_skills_section` (234-254) → `_write_skills(sprite, skills, runtime_name)`
    (one fs write per skill)
- Template to shrink: `src/agent_on_demand/run_agent.sh.tmpl` — drop all
  `@@FIRST_RUN_BODY@@`, `@@MCP_SECTION@@`, `@@SKILLS_SECTION@@`, sentinel logic,
  env var block, and API key export. Keep: strictness, ERR trap, prompt read,
  dispatcher.
- Tests that assert wrapper-script substrings: `tests/test_tools_mcp.py`,
  `tests/test_skills.py`, `tests/test_runtimes.py`, `tests/test_resources.py`,
  `tests/test_environments.py`.

## Current State Analysis

- **View knows about scripts.** `_create_session` imports `build_wrapper_script`,
  `EnvironmentSetup`, `RepoSpec`, `McpServerSpec`, `SkillSpec` directly. The
  service accepts an opaque `wrapper_script: str`.
- **All setup happens on Sprite-side shell execution.** Exit code granularity is
  "whole script succeeded or failed at the trap." Package install errors are
  indistinguishable from clone errors in the exit code.
- **API key lives in the script body.** `shlex.quote`'d into an `export` line at
  the top of `/run-agent.sh`. The script sits on disk for the lifetime of the
  session.
- **Sentinel gate `/tmp/aod-initialized`** exists purely because the script does
  double duty: it both sets up and runs. Remove setup from the script → remove
  the sentinel.
- **`.gemini` mkdir happens every invocation** (line 43 of the template). Harmless
  but not right — should run once during provision.
- **MCP config and skill files are rewritten every invocation.** This is documented
  as idempotent-by-design but is effectively wasted work after the first turn.

## Desired End State

1. `session_service.provision_session(user, spec)` takes exactly one positional
   spec; the view builds it from request data and never touches `build_wrapper_script`.
2. Each setup stage that can fail independently — packages, clone, user setup,
   MCP write, skills write, env write, script write — is its own
   `sprite.command()` or filesystem write with a dedicated `ProvisionError`
   wrapping the stage name.
3. `/run-agent.sh` on the Sprite is a ~20-line dispatcher: source env, read
   prompt, exec the runtime CLI. No setup, no sentinel, no package managers.
4. The runtime API key lives in `/tmp/aod-env` (mode 0600), sourced at turn
   time. No API key string appears in `/run-agent.sh`.
5. Views pass a single `SessionSpec` in, call `run_turn` to launch a turn.
   `write_prompt` becomes an internal detail.
6. All existing tests pass. Test structure shifts: fewer "wrapper script contains
   substring X" tests, more "calling `provision_session(spec)` produces the
   following sequence of sprite.command/fs.write calls" tests using a recording
   fake.

### Verification

- `make lint && make fmt` clean.
- `make test` passes. Test counts stay roughly even; script-substring assertions
  are replaced with stage-invocation assertions against a fake Sprite.
- Manual smoke via `make dev` + `curl`: create a session, verify turn 1 runs,
  verify a `continue` on the same session runs, inspect the Sprite via
  `sprite.command("ls", "-la", "/tmp/aod-env")` for 0600 permissions.
- `AOD_API_TOKEN=… make test-e2e-fast` passes. **Required** before landing —
  session execution paths are exactly what this refactor touches.
- Manual debug probe: introduce a bogus package in an Environment, run a session,
  confirm the failure surfaces as a stage-tagged `ProvisionError` instead of a
  generic "Session failed" after the whole script aborts.

## What We're NOT Doing

- **Not changing the `/run-agent.sh` dispatcher's mode dispatch.** `run` vs
  `continue` semantics, `--dangerously-skip-permissions` asymmetry (Muddy Zone
  7), and Claude's `--session-id`/`--resume` handoff stay identical.
- **Not fixing the `cmd_thread.join(timeout=5.0)` zombie window** (Muddy Zone 8).
- **Not fixing `write_prompt` orphan turns** (Muddy Zone 3).
- **Not adding server-side timeout enforcement.**
- **Not changing any model, migration, or HTTP response shape.** `ProvisionError`
  carries a `stage` attribute for server-side logging only; it is **not**
  included in 502 response bodies.
- **Not changing the on-Sprite layout of skills, MCP configs, or env vars.**
  Paths stay: `~/.claude/skills/…`, `/tmp/mcp.json`, etc.
- **Not keeping `sprites_exec.py`**. Contents move into the new `session_service/`
  package; the file is deleted in this PR.

## Implementation Approach

One phase. Four sequential steps because each depends on the previous:

1. Define the new public surface (`SessionSpec`, plus rehomed `RepoSpec`,
   `McpServerSpec`, `SkillSpec`, `EnvironmentSetup`) in `session_service.py`.
2. Write the per-stage helpers in `session_service.py`. Rewrite
   `/run-agent.sh.tmpl` to the dispatcher form. Rewrite `build_wrapper_script`
   (or replace it with a private `_render_dispatcher_script`) to just render
   the dispatcher.
3. Rewrite `provision_session(user, spec)` to orchestrate the stages. Add a
   `run_turn(sprite, prompt, mode, timeout)` that rolls up `write_prompt` +
   `start_turn`.
4. Flip call sites in `views/sessions.py` and update the tests.

Tests that currently assert wrapper-script substrings will be rewritten to drive
`provision_session` against a recording fake Sprite. The fake lives in
`tests/fakes/sprite.py` (new) and records the sequence of `command()` /
`filesystem().write_text()` calls for assertion.

## File Ownership Map

`session_service.py` becomes the package `session_service/`:

```
src/agent_on_demand/session_service/
    __init__.py          # public surface: errors, SessionSpec, provision_session,
                         # run_turn, resume_session, destroy_session, get_client,
                         # and re-exports of RepoSpec / McpServerSpec / SkillSpec /
                         # EnvironmentSetup
    errors.py            # SessionServiceError, NoSpritesKeyError, ProvisionError,
                         # SessionHandleNotFound
    specs.py             # SessionSpec, RepoSpec, McpServerSpec, SkillSpec,
                         # EnvironmentSetup
    client.py            # get_client, _require_client
    provisioning.py      # provision_session + private _install_packages,
                         # _clone_repos, _run_user_setup, _write_mcp_config,
                         # _write_skills, _write_env_file, _write_run_script,
                         # _apply_network_policy, _best_effort_delete
    dispatcher.py        # _render_dispatcher_script (pure string render of the
                         # tiny run-agent.sh), _mcp_cmd_flags
    turn.py              # write_prompt, start_turn, run_turn
```

| File | Change Type | Notes |
|------|-------------|-------|
| `src/agent_on_demand/session_service.py` | delete | Replaced by `session_service/` package below. |
| `src/agent_on_demand/session_service/__init__.py` | create | Re-export the public names listed above so existing `from agent_on_demand import session_service` call sites keep working. |
| `src/agent_on_demand/session_service/errors.py` | create | Move error classes; add `stage: str` attribute on `ProvisionError` (internal, not exposed in HTTP responses). |
| `src/agent_on_demand/session_service/specs.py` | create | `SessionSpec` (new) plus rehomed `RepoSpec`, `McpServerSpec`, `SkillSpec`, `EnvironmentSetup`. |
| `src/agent_on_demand/session_service/client.py` | create | `get_client`, `_require_client`, `_best_effort_delete`. |
| `src/agent_on_demand/session_service/provisioning.py` | create | `provision_session(user, spec)` + per-stage helpers. |
| `src/agent_on_demand/session_service/dispatcher.py` | create | `_render_dispatcher_script(runtime, has_mcp)` and MCP flag helper; owns the template read + substitution. |
| `src/agent_on_demand/session_service/turn.py` | create | `write_prompt`, `start_turn`, `run_turn(session, turn, sprite, prompt, mode, timeout)`. |
| `src/agent_on_demand/sprites_exec.py` | delete | Dataclasses move to `specs.py`, all `_build_*` helpers move to `provisioning.py`/`dispatcher.py` and change signature (emit commands/fs writes instead of shell strings). |
| `src/agent_on_demand/run_agent.sh.tmpl` | rewrite | Collapse to ~20 lines: strictness, trap, `source /tmp/aod-env`, prompt read, case dispatch on mode. Remove sentinel, FIRST_RUN_BODY, MCP_SECTION, SKILLS_SECTION, API_KEY_EXPORT, ENV_VARS_BLOCK. |
| `src/agent_on_demand/views/sessions.py` | modify | `_create_session`: replace `build_wrapper_script(...)` + `provision_session(wrapper_script=...)` with `provision_session(user, spec)`; call `run_turn(...)` instead of `start_turn`. `send_prompt`: replace `write_prompt + start_turn` with `run_turn`. Imports pull spec dataclasses from `session_service`. |
| `src/agent_on_demand/stream.py` | no change | Still invokes `bash /run-agent.sh <mode>`. Dispatcher contract is unchanged. |
| `tests/fakes/sprite.py` | create | `RecordingSprite` / `RecordingSpritesClient` that captures `.command()` args, `.filesystem().write_text()` targets, and `.update_network_policy()` calls for assertion. |
| `tests/test_session_service.py` | create | Unit tests: `provision_session(spec)` → recorded call sequence. One test per stage (packages, clone with/without token, user setup, mcp by runtime, skills by runtime, env file permissions, run script rendering). One cleanup test (SpriteError mid-stage → delete called). |
| `tests/test_tools_mcp.py` | modify | Rewrite wrapper-script assertions → assertions against recorded stage calls. |
| `tests/test_skills.py` | modify | Same. |
| `tests/test_runtimes.py` | modify | Same. |
| `tests/test_resources.py` | modify | Same. |
| `tests/test_environments.py` | modify | Same. |

## Phase 1: Reshape session_service + rewrite dispatcher

### Changes Required

#### 1. `src/agent_on_demand/session_service.py` — new public surface

Rehome the four specs from `sprites_exec.py` (unchanged fields). Add the new top-level
input dataclass:

```python
@dataclass(frozen=True)
class SessionSpec:
    """Ingredients for a session. Service decides how to realize them on the Sprite."""
    name: str
    runtime: RuntimeConfig
    api_key: str
    runtime_session_id: str | None
    environment: Environment | None
    repos: list[RepoSpec]
    mcp_servers: list[McpServerSpec]
    skills: list[SkillSpec]
```

Error subclass carries stage info for **server-side logging only** — never
serialized into the HTTP response body:

```python
class ProvisionError(SessionServiceError):
    """Sprites rejected a provision operation. `stage` is for logging only
    and is never sent to API clients."""
    def __init__(self, message: str, *, stage: str):
        super().__init__(message)
        self.stage = stage
```

Views that catch `ProvisionError` can log `e.stage` via `logger.warning(...)`
but must return `{"detail": str(e)}` (no `stage` key) to preserve the current
502 body shape.

#### 2. Per-stage helpers in `session_service.py`

Each helper does one thing and raises `ProvisionError(stage=...)` on failure.
They all take a live `Sprite` and the relevant slice of the spec.

```python
def _apply_network_policy(sprite: Sprite, env: Environment | None) -> None: ...
def _write_env_file(sprite: Sprite, spec: SessionSpec) -> None:
    """Write /tmp/aod-env with the runtime API key + Environment.env_vars.
    chmod 600. Shell-quoted KEY=VALUE lines, one per var, safe to `source`."""
def _install_packages(sprite: Sprite, env: Environment | None) -> None:
    """One sprite.command() per package manager that has entries.
    Exit codes per manager surface as ProvisionError(stage='packages.apt') etc."""
def _clone_repos(sprite: Sprite, repos: list[RepoSpec]) -> None:
    """Write /tmp/.git-credentials (mode 600) if any repos carry tokens,
    sprite.command(git clone) per repo, sprite.command(rm) in finally."""
def _run_user_setup(sprite: Sprite, env: Environment | None) -> None:
    """Pipe env.setup_script into `bash -s` via sprite.command stdin."""
def _write_mcp_config(sprite: Sprite, spec: SessionSpec) -> None:
    """Runtime-dispatched fs write: Claude → /tmp/mcp.json; Codex →
    ~/.codex/config.toml; Gemini → ~/.gemini/settings.json."""
def _write_skills(sprite: Sprite, skills: list[SkillSpec], runtime_name: str) -> None:
    """One filesystem().write_text() per skill at
    <root>/<name>/SKILL.md where <root> is runtime-specific."""
def _write_run_script(sprite: Sprite, runtime: RuntimeConfig, has_mcp: bool) -> None:
    """Render the dispatcher template and write to /run-agent.sh + chmod +x."""
```

Helper ordering in `provision_session` (matters — user setup depends on
packages; clone's credentials file must be written before git; etc.):

```
network policy → env file → packages → clone → user setup →
mcp config → skills → run script
```

#### 3. `/run-agent.sh.tmpl` — dispatcher only

```bash
#!/bin/bash
set -Eeuo pipefail
__aod_on_err() { echo "AOD_STAGE_FAILED: exit=$1 line=$2 command=$3" >&2; }
trap '__aod_on_err $? ${LINENO} "${BASH_COMMAND}"' ERR

set -a; source /tmp/aod-env; set +a

MODE="${1:-run}"
if [ ! -f @@PROMPT_FILE_PATH@@ ]; then
    echo "missing prompt file: @@PROMPT_FILE_PATH_RAW@@" >&2
    exit 2
fi
PROMPT=$(cat @@PROMPT_FILE_PATH@@)
export PROMPT

cd /home/sprite

case "$MODE" in
    run)      exec @@RUN_CMD@@ ;;
    continue) exec @@CONTINUE_CMD@@ ;;
    *)        echo "unknown mode: $MODE" >&2; exit 2 ;;
esac
```

Template substitution shrinks to three tokens: `@@PROMPT_FILE_PATH@@`,
`@@PROMPT_FILE_PATH_RAW@@`, `@@RUN_CMD@@`, `@@CONTINUE_CMD@@`. MCP flags are
baked into RUN/CONTINUE_CMD at render time via the existing `_mcp_cmd_flags`
logic (migrated into `session_service._render_dispatcher_script`).

Removed: `@@API_KEY_EXPORT@@`, `@@SESSION_ID_EXPORT@@`, `@@ENV_VARS_BLOCK@@`,
`@@INIT_SENTINEL_PATH@@`, `@@FIRST_RUN_BODY@@`, `@@MCP_SECTION@@`,
`@@SKILLS_SECTION@@`, the entire sentinel-gated setup block, the EXIT trap that
cleans `/tmp/.git-credentials` (cleanup moves into `_clone_repos`).

`AOD_SESSION_ID` moves into the env file. Runtime commands in `runtimes.py`
already reference `$AOD_SESSION_ID`; sourcing the env file exports it.

#### 4. `provision_session` — new orchestration

```python
def provision_session(user, spec: SessionSpec) -> Sprite:
    client = _require_client(user)
    try:
        sprite = client.create_sprite(spec.name)
    except SpriteError as e:
        raise ProvisionError(f"create_sprite: {e}", stage="create") from e
    try:
        _apply_network_policy(sprite, spec.environment)
        _write_env_file(sprite, spec)
        _install_packages(sprite, spec.environment)
        _clone_repos(sprite, spec.repos)
        _run_user_setup(sprite, spec.environment)
        _write_mcp_config(sprite, spec)
        _write_skills(sprite, spec.skills, spec.runtime.name)
        _write_run_script(sprite, spec.runtime, has_mcp=bool(spec.mcp_servers))
    except ProvisionError:
        _best_effort_delete(client, spec.name)
        raise
    except SpriteError as e:
        _best_effort_delete(client, spec.name)
        raise ProvisionError(f"unexpected: {e}", stage="unknown") from e
    return sprite
```

#### 5. `run_turn` — rollup for call sites

```python
def run_turn(
    session: AgentSession,
    turn: SessionTurn,
    sprite: Sprite,
    prompt: str,
    mode: str,
    timeout: float,
) -> None:
    """Write the per-turn prompt and spawn the background execution thread."""
    write_prompt(sprite, prompt)   # still internal; keep as module fn for testability
    start_turn(session, turn, sprite, mode, timeout)
```

`write_prompt` and `start_turn` stay as private-ish module functions — tests can
call them individually. Views always use `run_turn`.

#### 6. `views/sessions.py` — call-site flip

`_create_session` loses the `build_wrapper_script` call and the spec imports
from `sprites_exec`:

```python
from agent_on_demand.session_service import (
    EnvironmentSetup, McpServerSpec, RepoSpec, SessionSpec, SkillSpec,
)

...

env_setup = None
if environment_obj:
    env_setup = EnvironmentSetup(
        packages=environment_obj.packages,
        env_vars=environment_obj.env_vars,
        setup_script=environment_obj.setup_script,
    )

spec = SessionSpec(
    name=name,
    runtime=config,
    api_key=api_key,
    runtime_session_id=runtime_session_id,
    environment=environment_obj,
    repos=_resources_to_repo_specs(req.resources),
    mcp_servers=_mcp_servers_to_specs(agent_obj.mcp_servers),
    skills=_skills_to_specs(agent_obj.skills),
)
try:
    sprite = session_service.provision_session(request.user, spec)
except session_service.NoSpritesKeyError as e:
    return JsonResponse({"detail": str(e)}, status=400)
except session_service.ProvisionError as e:
    logger.warning("provision failed at stage=%s: %s", e.stage, e)
    return JsonResponse({"detail": str(e)}, status=502)

# ... (DB writes unchanged) ...

session_service.run_turn(session, turn, sprite, effective_prompt, "run", float(req.timeout))
```

`send_prompt` continue path (`views/sessions.py:432-437`) collapses to:

```python
try:
    session_service.run_turn(session, turn, sprite, req.prompt, "continue", float(req.timeout))
except session_service.ProvisionError as e:
    logger.warning("prompt write failed at stage=%s: %s", e.stage, e)
    return JsonResponse({"detail": str(e)}, status=502)
```

502 response body shape is unchanged from today — `stage` is logged server-side
only.

#### 7. `tests/fakes/sprite.py` — recording fake

```python
@dataclass
class RecordedCommand:
    argv: tuple[str, ...]
    stdin: bytes | None
    timeout: float | None

@dataclass
class RecordedWrite:
    path: str
    text: str
    mode: int | None

class RecordingSprite:
    def __init__(self, name: str):
        self.name = name
        self.commands: list[RecordedCommand] = []
        self.writes: list[RecordedWrite] = []
        self.policies: list[NetworkPolicy] = []
        self._next_raises: Exception | None = None
    def command(self, *argv, timeout=None): ...   # returns a recorder that captures run()
    def filesystem(self): ...                     # returns a path-like that records writes
    def update_network_policy(self, policy): self.policies.append(policy)
    def raise_on_next(self, exc: Exception): self._next_raises = exc

class RecordingSpritesClient:
    def __init__(self): self.sprites: dict[str, RecordingSprite] = {}
    def create_sprite(self, name): ...
    def delete_sprite(self, name): ...
    def get_sprite(self, name): ...
```

Exposed via a `pytest` fixture in `tests/conftest.py` that monkeypatches
`session_service.get_client` to return a `RecordingSpritesClient`.

#### 8. Test migration

For each of `test_tools_mcp.py`, `test_skills.py`, `test_runtimes.py`,
`test_resources.py`, `test_environments.py`: replace `build_wrapper_script(...)`
call + `assert "substring" in script` pattern with:

```python
spec = SessionSpec(name="s", runtime=RUNTIMES["claude"], api_key="k", ...)
provision_session(user, spec)

writes = {w.path: w.text for w in fake_sprite.writes}
assert "/tmp/mcp.json" in writes
assert '"name": "github"' in writes["/tmp/mcp.json"]

cmds = [c.argv for c in fake_sprite.commands]
assert ("apt-get", "update", "-qq") in cmds  # via subprocess parsing helper
```

One new file `tests/test_session_service.py` covers the service-level contract:
order of operations, failure propagation with stage tag, best-effort cleanup,
env-file permissions, dispatcher rendering.

### Verification

After all changes:

1. `make lint && make fmt` clean.
2. `make test` — unit tests pass, including the new stage-assertion tests.
3. `make dev` + `curl -X POST /sessions ...` with a known-good agent/env — session
   reaches `running`, turn 1 completes, `POST /prompt` runs turn 2.
4. Manual failure-injection probe: add a nonexistent apt package to the Environment,
   POST a session, observe the 502 response body now carries `"stage": "packages.apt"`.
5. `make test-e2e-fast` passes. Mandatory gate before merge.

## Risks

- **Provision latency grows.** Current: 2 Sprites WS roundtrips (create + bulk
  fs write). New: up to ~8 roundtrips (create + 6-7 stages). Expected impact:
  single-digit seconds added to session provision. Acceptable because HTTP
  returns 202 immediately and the user-visible latency is dominated by the
  agent's own startup. Measure once in e2e before landing.
- **Partial-state on stage failure.** Same as today (delete the Sprite, re-raise).
  No new cleanup complexity — per-stage helpers don't need to roll back, only
  the final `_best_effort_delete` matters.
- **Test churn is large** across five existing modules. Mitigated by the shared
  recording-fake fixture — the test rewrites end up mechanical.

## Decisions Locked

- `session_service.py` splits into the `session_service/` package described in
  the File Ownership Map — done in this PR.
- `sprites_exec.py` is deleted in this PR. No re-export shim.
- `ProvisionError.stage` is an internal attribute only. 502 response bodies
  stay exactly as they are today.
- Views always go through `run_turn`; `write_prompt` and `start_turn` are
  internal-only and not re-exported from the package.

## Related

- Research: `thoughts/research/2026-04-18-sprites-script-setup.md`
- Prior refactor precedent: commit `c29cec9` (models.py → package split) and
  `b4aa60d` (extracted run-agent.sh into a template) — same codebase, same style.
- Memory: `project_session_service_refactor.md` captures the endorsed direction.
