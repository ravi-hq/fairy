---
date: 2026-04-18T00:00:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 93dae68dea061bcef3c8bd484291e93be52b4ca5
branch: main
repository: ravi-hq/agent-on-demand
topic: "Abstracting Sprites as an implementation of a SessionBackend interface for session lifecycle"
tags: [research, team-research, sprites, session-backend, abstraction, testing]
status: complete
method: agent-team
team_size: 4
tracks: [lifecycle-touchpoints, library-surface, test-strategy, prior-art]
last_updated: 2026-04-18
last_updated_by: Claude Code
---

# Research: Abstracting Sprites behind a SessionBackend interface

**Date**: 2026-04-18
**Researcher**: Claude Code (team-research)
**Git Commit**: [`93dae68`](https://github.com/ravi-hq/agent-on-demand/commit/93dae68dea061bcef3c8bd484291e93be52b4ca5)
**Branch**: `main`
**Repository**: ravi-hq/agent-on-demand
**Method**: Agent team (4 specialist researchers)

## Research Question

> I would like to abstract Sprites as an implementation of the interface needed to actually deal with the lifecycle of a session.

## Summary

Sprites is the only session backend today and no prior doc anticipates a second one, so the interface can be designed from the call sites rather than from hypothetical alternatives. The full dependency on `sprites-py==0.0.1-rc37` reduces to **15 call sites across 4 files** and collapses cleanly into a **7-method `SessionBackend` protocol** (`provision`, `resume`, `apply_network_policy`, `write_file`, `set_executable`, `run_command`, `destroy`), plus a small error taxonomy and a `WorkspaceFS` sub-protocol. Two structural issues fall out for free: `session.sprite_name` is a backend-specific detail leaking into the DB schema (should become an opaque `session_handle`), and `SpritesClient` is constructed inline per-request without `.close()` being called, leaking `httpx.Client` instances. The biggest practical win is testing: the current `threading.Thread` + inline `mock_sprites` pattern is duplicated 6 times across test files and still leaves the `pending→running→completed` state machine covered only in e2e; a synchronous in-process backend makes state transitions, streaming output, and failure cleanup all unit-testable. Five hard constraints carry over from prior work — multi-turn session reuse, persistent filesystem between turns, fairy-side output capture, post-creation network policy, and no leakage of secrets into exec-time env params.

## Research Tracks

### Track 1: Lifecycle Touchpoints
**Researcher**: lifecycle-researcher
**Scope**: `src/agent_on_demand/{views,stream,signals,admin,sprites_exec,runtimes}.py`, `src/config/settings.py`

#### Findings

1. **Client construction** — `SpritesClient(token, base_url=settings.SPRITES_BASE_URL)` is created inline per-request at `src/agent_on_demand/views.py:53`, `src/agent_on_demand/signals.py:19`, `src/agent_on_demand/admin.py:232`. No caching, no cleanup.
2. **Provision: create** — `client.create_sprite(name)` where `name = f"{SPRITE_NAME_PREFIX}-{uuid4().hex[:12]}"` (`src/agent_on_demand/views.py:269,275`).
3. **Provision: resume** — `client.get_sprite(session.sprite_name)` at `src/agent_on_demand/views.py:446`, called on every `POST /sessions/{id}/prompt`.
4. **Apply network policy** — `sprite.update_network_policy(NetworkPolicy(rules=[PolicyRule(...), ..., PolicyRule(domain="*", action="deny")]))` at `src/agent_on_demand/views.py:282`; skipped entirely when `networking_type != "limited"`.
5. **Workspace handle** — `sprite.filesystem()` at `src/agent_on_demand/views.py:284`, `views.py:451`. Only ever used as a prerequisite to `write_text`.
6. **Write wrapper script (initial run)** — `(fs / "run-agent.sh").write_text(script)` at `src/agent_on_demand/views.py:311`.
7. **Write wrapper script (multi-turn)** — same call at `src/agent_on_demand/views.py:475`; only env_vars re-exported, packages/setup_script skipped by design.
8. **Mark executable** — `sprite.command("chmod", "+x", "/run-agent.sh").run()` at `src/agent_on_demand/views.py:312`. Only on initial provision.
9. **Run command — construct** — `cmd = sprite.command("bash", "/run-agent.sh", timeout=timeout)` at `src/agent_on_demand/stream.py:71`.
10. **Run command — stdout/stderr sinks** — `cmd.stdout = TaggingQueueWriter(...)`, `cmd.stderr = TaggingQueueWriter(...)` at `src/agent_on_demand/stream.py:72-73`.
11. **Run command — blocking exec** — `cmd.run()` at `src/agent_on_demand/stream.py:74`.
12. **Destroy (terminate)** — `client.delete_sprite(session.sprite_name)` at `src/agent_on_demand/views.py:527`; `session.sprite_name` is then cleared to `""`.
13. **Destroy (compensating rollback)** — best-effort `client.delete_sprite(name)` at `src/agent_on_demand/views.py:315` when prepare fails after create.
14. **Destroy (DB cascade)** — `pre_delete` signal on `AgentSession` calls `client.delete_sprite(instance.sprite_name)` at `src/agent_on_demand/signals.py:36`; reached via `session.delete()` at `src/agent_on_demand/views.py:558` and the admin bulk action at `src/agent_on_demand/admin.py:245`.
15. **Error mapping** — `SpriteError`: `create_sprite`→502, prepare block→502 + rollback, `get_sprite`→404, `delete_sprite`→logger.warning only (`src/agent_on_demand/views.py:276-318,447-477,528-529`). `ExecError`: `e.exit_code()` (method, not property — explicitly noted in `src/agent_on_demand/stream.py:77-78`) stored to `session.exit_code`, status → `failed` if non-zero.

Additional note from the cross-reference with Track 3: the full `pending→running→completed/failed` state machine in `run_session_background` (`src/agent_on_demand/stream.py:85-129`) is never exercised at the unit level because `threading.Thread` is mocked in every test that hits `POST /sessions`; only the `ExecError→failed` branch is covered, via a single direct call.

#### Proposed lifecycle operations (7-method SessionBackend protocol)

| # | Operation | Replaces | Notes |
|---|---|---|---|
| 1 | `provision(name) -> session_handle` | `create_sprite` | Opaque handle for subsequent calls |
| 2 | `resume(session_handle) -> session_handle` | `get_sprite` | Raises backend-specific error if no longer available |
| 3 | `apply_network_policy(handle, policy)` | `update_network_policy` | Backend-neutral policy type; translation is the backend's job |
| 4 | `write_file(handle, path, content)` | `filesystem()` + `(fs / path).write_text(content)` | Absorbs the two-step pattern |
| 5 | `set_executable(handle, path)` | `command("chmod", "+x", path).run()` | Blocking; setup only |
| 6 | `run_command(handle, command, args, *, timeout, stdout, stderr) -> int` | `command(..., timeout=t)` + stdout/stderr assignment + `run()` | Returns exit code; no need for callers to catch `ExecError` for exit codes |
| 7 | `destroy(handle)` | all `delete_sprite` call sites | Best-effort; log but don't raise |

### Track 2: Sprites Library Surface
**Researcher**: library-surface-researcher
**Scope**: `.venv/lib/python3.14/site-packages/sprites/`, pinned at `sprites-py==0.0.1-rc37`

#### Findings

1. **Top-level exports** — classes (`SpritesClient`, `Sprite`, `SpriteFilesystem`, `SpritePath`, `ControlConnection`, `OpConn`), 11 exceptions (`SpriteError`, `NetworkError`, `AuthenticationError`, `NotFoundError`, `ExecError`, `FilesystemError`, `FileNotFoundError_`, `IsADirectoryError_`, `NotADirectoryError_`, `PermissionError_`, `DirectoryNotEmptyError`), and a wide set of dataclass types (`NetworkPolicy`, `PolicyRule`, `ExecOptions`, `ExecResult`, `SpawnOptions`, etc.). (`.venv/.../sprites/__init__.py`)
2. **`SpritesClient.__init__(token, base_url="https://api.sprites.dev", timeout=30.0, control_mode=False)`** — creates an `httpx.Client`, supports `with ... as client:` (calls `close()`), but the app does not use it that way. (`.venv/.../sprites/client.py`)
3. **`create_sprite(name, config=None) -> Sprite`** — POST `/v1/sprites`, 120s timeout, raises `NetworkError | SpriteError | AuthenticationError | NotFoundError`.
4. **`get_sprite(name) -> Sprite`** — GET `/v1/sprites/{name}`, raises `NetworkError | NotFoundError | AuthenticationError | SpriteError`.
5. **`delete_sprite(name) -> None`** — DELETE `/v1/sprites/{name}`, expects 204; raises `NetworkError | SpriteError`.
6. **`sprite.filesystem(working_dir="/") -> SpriteFilesystem`** — pure factory, no API call. Not a `pathlib.Path` subclass. Supports `fs / "path"` → `SpritePath`. `SpritePath` has a full pathlib-like surface: `write_text`, `write_bytes`, `read_text`, `read_bytes`, `exists`, `is_file`, `is_dir`, `stat`, `iterdir`, `listdir`, `mkdir`, `unlink`, `rmdir`, `rmtree`, `rename`, `replace`, `copy_to`, `chmod(mode, recursive=False)`, `touch`.
7. **`sprite.command(*args, env=None, cwd=None, timeout=None) -> Cmd`** — pure factory. The app always assigns `cmd.stdout` / `cmd.stderr` as `io.RawIOBase` writers after construction (`TaggingQueueWriter`). The library drives the WebSocket runner and calls `.write(bytes)` on the assigned sinks.
8. **`Cmd.run() -> None`** — synchronous (drives an `asyncio` loop internally). Raises `ExecError` (alias `ExitError`) if exit != 0; `TimeoutError` if the timeout fires. After run, `cmd.exit_code` (a **property** on `Cmd`) returns the integer exit code.
9. **`ExecError(message, exit_code, stdout=b"", stderr=b"")`** — **`exit_code()` is a method on `ExecError`** (not a property), which is inconsistent with `Cmd.exit_code`. The app hits this at `src/agent_on_demand/stream.py:78` and has a code comment flagging it.
10. **`sprite.update_network_policy(policy: NetworkPolicy) -> None`** — POST `/v1/sprites/{name}/policy/network` with `{"rules": [...]}`.
11. **Pinned version** — `sprites-py==0.0.1-rc37` (from `pyproject.toml` and `sprites/__init__.py:__version__`).
12. **Resource-leak surface** — `SpritesClient.close()` closes the underlying `httpx.Client`. The app creates clients inline per-request in `views.py`, `signals.py`, `admin.py` and never calls `close()`. No context manager usage anywhere.
13. **No higher-level streaming API** — the `cmd.stdout = binary_io` assignment pattern is the intended streaming API; no iterator-based alternative exists.
14. **Chmod has two paths** — `SpritePath.chmod(mode, recursive=False)` exists as a filesystem operation, but the app chose to shell out via `sprite.command("chmod", "+x", ...).run()`. The abstraction can expose `chmod` on the workspace protocol so backends don't need a shell command for this.

#### Primitives the interface must wrap

| Sprites primitive | Abstract counterpart |
|---|---|
| `SpritesClient(token, base_url)` | `Backend.create_client(token) -> BackendClient` |
| `client.create_sprite(name)` | `BackendClient.provision(name) -> SessionHandle` |
| `client.get_sprite(name)` | `BackendClient.get(name) -> SessionHandle` |
| `client.delete_sprite(name)` | `BackendClient.destroy(name)` |
| `sprite.filesystem()` | `SessionHandle.workspace() -> WorkspaceFS` |
| `(fs / path).write_text(content)` | `WorkspaceFS.write_text(path, content)` |
| `(fs / path).chmod(mode)` | `WorkspaceFS.chmod(path, mode)` |
| `sprite.command(*args, timeout=t)` | `SessionHandle.make_command(*args, timeout) -> Command` |
| `cmd.stdout = w; cmd.stderr = w` | `Command.set_output(stdout: BinaryIO, stderr: BinaryIO)` |
| `cmd.run()` (raises `ExecError` non-zero) | `Command.run() -> int` (return exit code directly) |
| `cmd.exit_code` property | `Command.exit_code: int` |
| `ExecError.exit_code()` (method!) | `ExecutionError.exit_code: int` (property — normalize) |
| `sprite.update_network_policy(NetworkPolicy)` | `SessionHandle.set_network_policy(rules)` |
| `NetworkPolicy` / `PolicyRule` | Keep dataclasses; they are already backend-neutral enough |
| `SpriteError` | `BackendError` (base) |
| `NotFoundError` | `SessionNotFoundError(BackendError)` |

### Track 3: Test Strategy & Current Mocking
**Researcher**: test-strategy-researcher
**Scope**: `tests/`, `tests/e2e/`, `Makefile`

#### Findings

1. **6-way duplication of a `mock_sprites` fixture** — the same `mocker.patch("agent_on_demand.views._get_client", return_value=mock_client) + mocker.patch("agent_on_demand.views.threading.Thread")` pattern appears inline at `tests/test_api.py:220`, `tests/test_api.py:383`, and as a fixture in `tests/test_resources.py:67-80`, `tests/test_environments.py:94-107`, `tests/test_networking_wiring.py:63-75`, `tests/test_skills.py:66-77`, `tests/test_tools_mcp.py:49-60`. There is no shared `conftest.py` version.
2. **Background runner never exercised via HTTP** — every `POST /sessions` unit test mocks `threading.Thread` to a `MagicMock`, so the `pending→running→completed/failed` state machine in `run_session_background` is never driven end-to-end at the unit level.
3. **One direct background call** — `tests/test_api.py:430-448` calls `run_session_background` directly with a mock sprite whose `command().run()` raises `sprites.ExecError(exit_code=1)`. This is the only unit test that exercises any background-runner behavior.
4. **Error-path coverage is sparse** — `create_sprite` failures are never simulated (only `update_network_policy` failure at `tests/test_networking_wiring.py:171` uses a real `SpriteError` via `side_effect`). Filesystem write failures are never simulated (`mock_fs.write_text` is always a no-op).
5. **E2E tier carries the full lifecycle burden** — `tests/e2e/test_sessions.py`, `test_skills.py`, `test_mcp.py`, `test_environments.py` are the only tests that see a real session go `pending→running→completed`. `@pytest.mark.slow` is used to gate the costly subset (`make test-e2e-fast` skips them).
6. **Two sprite types imported directly in tests** — `sprites.NetworkPolicy` and `sprites.PolicyRule` at `tests/test_networking_wiring.py:6` are used to assert the exact object passed to `mock_sprite.update_network_policy`. The abstraction needs to either keep these types backend-neutral (possible — they are pure dataclasses) or translate them in the test assertions.
7. **Multi-turn re-patches `_get_client`** — `tests/test_skills.py:401`, `tests/test_tools_mcp.py:537` swap in a version where `get_sprite` (not `create_sprite`) is the mocked method. A `fake_session_backend` fixture could unify these paths.

#### Testing wins from abstraction

1. **Simulate `create_sprite` failure** → verify no orphaned session DB row and 502 response. Today impossible — the mock never raises on `create_sprite`.
2. **Drive `pending→running→completed` synchronously** → assert `GET /sessions/{id}` returns `status=completed, exit_code=0`. Today requires e2e.
3. **Inject `ExecError(exit_code=137)` through a fake backend** → verify `session.status=failed`, `exit_code=137`, and that SSE emits `{"type":"error"}`. Today the direct-call test covers DB writes but not the SSE surface.
4. **Simulate filesystem write failure** (`write_text` raises `OSError`) → verify graceful session-fail and sprite cleanup. Today uncoverable.
5. **`POST /sessions/{id}/prompt` on a running session** → verify 409. Today only the "terminated" case is unit-tested.
6. **Live SSE for a running session** → today only completed-session replay is unit-tested.
7. **Cleanup when `delete_sprite` raises on terminate** → verify response still returns sensibly and DB state is consistent.
8. **Multi-turn assertions without re-patching `_get_client`** — a single `fake_session_backend` fixture would replace today's separate "create" vs "resume" mock shapes.

### Track 4: Prior Art & Historical Context
**Researcher**: prior-art-researcher
**Scope**: `thoughts/research/`, `thoughts/plans/`, `README.md`, `site/docs/`, `.claude/skills/`

#### Findings

1. **Sprites is the only backend ever contemplated.** No doc under `thoughts/` mentions abstraction, pluggable backends, or alternative execution hosts (Fly Machines, Firecracker, local Docker, k8s, E2B). The word "abstraction" does not appear in any prior-art doc.
2. **The wrapper-script pattern is canonized** — `thoughts/research/2026-04-15-sprites-platform-research.md` and `.claude/skills/sprites.md` both lock in: write `/run-agent.sh` via `sprite.filesystem()`, chmod via `sprite.command("chmod", ...)`, run via `sprite.command("bash", ...)`. Reason: passing `env=` to `sprite.command()` **replaces** the entire process environment (destroys PATH), and the key ends up in WebSocket URL query params. This is a hard constraint any abstraction must honor.
3. **Session execution is fully DB-backed and decoupled from HTTP** — `thoughts/plans/2026-04-16-session-based-execution.md` (shipped) has `POST /sessions` return 202 immediately, with a background thread dual-writing output into `AgentSessionLog`. `GET /sessions/{id}/stream` replays from DB then live-tails. Chosen explicitly over Celery, ASGI, and WebSockets.
4. **Sprites has no server-side output storage** — `sprite.attach_session()` streams forward-only; `list_sessions()` returns metadata only. Fairy captures all output itself, which the current implementation does via `TaggingQueueWriter → queue → DB`. No backend can be assumed to provide output replay.
5. **Network policy is applied post-creation, not at creation** — `thoughts/research/2026-04-17-network-isolation.md` confirms `Sprite.update_network_policy(NetworkPolicy(...))` is called after `create_sprite(name)`. No creation-time network isolation parameter exists. Note also that this is currently only half-wired: fields exist and validate, but `update_network_policy()` is never called in `views.py` (tracked in `thoughts/plans/2026-04-17-network-isolation.md`).
6. **State machine is in force** — `pending → running → {completed, failed, terminated}`. Multi-turn is valid on non-running, non-terminated sessions; terminate is idempotent-error (409, not 200).
7. **SQLite WAL mode is a prerequisite** — default DELETE journal mode blocks readers during writes; the background log writer would stall the streaming endpoint. WAL was added as a required migration step.
8. **Multi-turn reuses the same Sprite, filesystem persists** — `.claude/skills/agent-on-demand-api.md` gotcha #4: setup scripts, packages, and env_vars do not re-run on `POST /sessions/{id}/prompt`; the filesystem persists. Any backend abstraction must support a "reuse existing container" path.

#### Constraints on the abstraction

- **C1. Multi-turn session reuse.** The same execution context must be addressable by `session_id` across multiple `POST /sessions/{id}/prompt` calls.
- **C2. Persistent filesystem between turns.** `/run-agent.sh` is rewritten every turn; packages installed by `setup_script` must survive between turns within a session lifetime.
- **C3. Output capture is fairy's responsibility, not the backend's.** Expose a streaming stdout/stderr channel; do not assume server-side replay.
- **C4. Network policy is post-creation, not a creation parameter.** Two-step flow: `provision(name)`, then `apply_network_policy(handle, policy)` if any.
- **C5. Secrets must not appear in exec-time env params.** Env vars must be injected via filesystem (wrapper script), not via `command(..., env={...})` — both because `env=` replaces the process environment and because it leaks keys into the transport URL.

## Cross-Track Discoveries

- **`session.sprite_name` is a backend-specific identifier leaking into the DB schema and HTTP error strings.** Track 1 found the field stores a string of the form `{SPRITE_NAME_PREFIX}-{uuid12}`; Track 2 confirmed this is exactly what Sprites expects in `get_sprite(name)`. With the abstraction, this should become an opaque `session_handle` column — the prefix is a fairy-side concern, not a Sprites-specific one. Error strings `"Failed to create Sprite: {e}"` and `"Sprite not found: {e}"` leak the same vocabulary into response bodies and should be neutralized.
- **Resource leak: `SpritesClient` is constructed per-request and never closed.** Track 2 flagged that `SpritesClient` owns an `httpx.Client` and supports context-manager close. Track 1 found 3 call sites (`views.py:53`, `signals.py:19`, `admin.py:232`) that all leak. The abstraction should centralize client lifecycle — either a cached per-user client or an explicit context-manager protocol on `BackendClient`.
- **`ExecError.exit_code()` is a method while `Cmd.exit_code` is a property** (Track 2 confirmed; Track 1 found the code comment flagging it at `stream.py:77-78`). The abstraction should normalize this — make `ExecutionError.exit_code` a plain `int` attribute and have `Command.run() -> int` return the exit code directly, so callers don't need to catch an exception to read the exit code.
- **`SpritePath.chmod()` exists but the app uses `sprite.command("chmod", "+x", ...).run()` instead** (Track 2). The abstraction can collapse this into `WorkspaceFS.chmod(path, mode)`, eliminating one `run_command` call site (Track 1 #8) and letting backends implement it however they want (shell-out, direct syscall, HTTP call).
- **The testing gap and the state-machine gap reinforce each other.** Track 3 observed that `threading.Thread` is mocked in every `POST /sessions` test. Track 1 observed that `run_session_background` owns the full state machine. Together they mean: the abstraction's real payoff is making the background path runnable synchronously in tests — which is why a `FakeBackend` implementation needs `run_command` to be drivable inline with deterministic exit codes.
- **`NetworkPolicy` / `PolicyRule` are already backend-neutral enough to keep as-is.** Track 2 confirmed they are plain dataclasses; Track 3 confirmed tests import them directly from `sprites`. The abstraction can re-export them from its own module (or define local copies with identical fields) without changing test assertions materially.

## Code References

| File | Tracks | Findings | Line(s) |
|------|--------|----------|---------|
| `src/agent_on_demand/views.py` | 1 | Client construction, provision, network policy, write, chmod, destroy, error mapping | 14, 53, 269, 275, 282, 284, 311-318, 446, 451, 475, 527, 558 |
| `src/agent_on_demand/stream.py` | 1 | Run command, stdout/stderr sinks, `ExecError.exit_code()` method note | 9, 71-80, 85-129 |
| `src/agent_on_demand/signals.py` | 1 | Destroy on session delete | 19, 36 |
| `src/agent_on_demand/admin.py` | 1 | Admin bulk destroy + second client construction | 222-247 |
| `src/agent_on_demand/runtimes.py` | 1 | Clean — no Sprites leakage | — |
| `src/agent_on_demand/sprites_exec.py` | 1 | Clean — no `sprites` imports; only shell-script assembly | — |
| `.venv/lib/python3.14/site-packages/sprites/__init__.py` | 2 | Top-level exports, pinned version | — |
| `.venv/lib/python3.14/site-packages/sprites/client.py` | 2 | `SpritesClient` signatures | — |
| `.venv/lib/python3.14/site-packages/sprites/sprite.py` | 2 | `Sprite.filesystem`, `command`, `update_network_policy` | — |
| `.venv/lib/python3.14/site-packages/sprites/exec.py` | 2 | `Cmd`, `Cmd.run`, `Cmd.exit_code` property | — |
| `.venv/lib/python3.14/site-packages/sprites/exceptions.py` | 2 | `ExecError.exit_code()` method; error taxonomy | — |
| `tests/test_api.py` | 3 | Inline mocks, direct `run_session_background` call | 220, 383, 426-448 |
| `tests/test_resources.py` | 3 | `mock_sprites` fixture | 67-80 |
| `tests/test_environments.py` | 3 | `mock_sprites` fixture | 94-107 |
| `tests/test_networking_wiring.py` | 3 | `mock_sprites` fixture, real `SpriteError` side_effect, direct import of `NetworkPolicy`/`PolicyRule` | 6, 63-75, 171 |
| `tests/test_skills.py` | 3 | `mock_sprites` fixture, multi-turn `_get_client` re-patch | 66-77, 401 |
| `tests/test_tools_mcp.py` | 3 | `mock_sprites` fixture, multi-turn re-patch | 49-60, 537, 555, 602 |

## Architecture Insights

- **The interface shape is driven almost entirely by the wrapper-script pattern.** Every call in the system reduces to: provision → (policy) → write one file → make it executable → run `bash /run-agent.sh` with streaming output → destroy. The 7-method protocol maps 1-to-1 to this flow; there is no operation that doesn't fit.
- **`SessionBackend` should be a `typing.Protocol`, not an ABC.** A Protocol lets the Sprites adapter be a lightweight wrapper that doesn't inherit from anything, and it lets a `FakeBackend` in tests be an ad-hoc class without formal subclassing. Django's dependency injection is shallow — a single `settings.SESSION_BACKEND` factory plus a module-level singleton is enough.
- **Two sub-protocols, not one flat interface.** Track 2's mapping distinguishes `BackendClient` (auth-scoped factory: provision/get/destroy) from `SessionHandle` (per-session ops: workspace, command, policy). That maps cleanly onto Sprites' `SpritesClient` + `Sprite` split. A third sub-protocol `WorkspaceFS` wraps `write_text` + `chmod`.
- **Error taxonomy is small: 3 exception classes suffice.** `BackendError` (base, analogous to `SpriteError`), `SessionNotFoundError` (analogous to `NotFoundError`, used for 404 on `resume`), and `ExecutionError` (analogous to `ExecError`, carries `exit_code: int`, `stdout: bytes`, `stderr: bytes`). Network/auth errors from the underlying transport can surface as the generic `BackendError`.
- **Sprites exception subclasses the fake backend should support injecting.** Track 3 surfaced that `ExecError(message, exit_code, stdout=b"", stderr=b"")` — with `.exit_code()` as a **method** not a property — is what the existing direct-call test already wires up (`tests/test_api.py:430`). `FilesystemError(message, operation, path)` is a `SpriteError` subclass, so write-failure tests would use it and hit the existing broad 502 handler in `views.py`. The precise 404 path on `get_sprite` is `NotFoundError`, not bare `SpriteError` — a distinction that is currently untested. `AuthenticationError` (token revoked mid-session) is not caught anywhere in app code and would bubble to 500; out of scope here but worth noting. The `FakeBackend` fixture's `inject_error(op, exc)` API should accept these specific subclasses so tests can exercise the right code paths.
- **The one operation that does NOT need to be in the protocol is `set_executable`.** It is a single call site (`views.py:312`) and could be collapsed into `WorkspaceFS.chmod(path, mode)`. That would cut the method count to 6. Recommend collapsing.
- **The background thread boundary is where the abstraction pays off.** `run_session_background` in `stream.py` currently takes a `Sprite` directly. Refactoring it to take a `SessionHandle` lets a synchronous fake backend drive the state machine in-process during tests — eliminating the `threading.Thread` mock entirely for most cases.

## Historical Context

- `thoughts/research/2026-04-15-sprites-platform-research.md` — the original "what is Sprites and how do we build on it" research. Establishes the wrapper-script pattern and the env-replacement gotcha.
- `thoughts/research/2026-04-16-sprites-deep-dive.md` — deeper look at session attach, no output replay, exec semantics.
- `thoughts/research/2026-04-16-session-based-execution.md` and `thoughts/plans/2026-04-16-session-based-execution.md` — decision to use background threads + DB-backed output + SSE replay; chose SQLite WAL mode; defined the state machine; introduced `cleanup_fn` pattern.
- `thoughts/research/2026-04-17-network-isolation.md` and `thoughts/plans/2026-04-17-network-isolation.md` — network policy is a post-create operation; the code path is currently half-wired (validated but not applied in `views.py`).
- `.claude/skills/sprites.md` — canonical wrapper-script pattern documented as a skill.
- `.claude/skills/agent-on-demand-api.md` — multi-turn semantics documented as a skill gotcha.

## Related Research

- `thoughts/research/2026-04-16-agent-tools-and-mcp.md`
- `thoughts/research/2026-04-17-agent-skills-support.md`
- `thoughts/research/2026-04-16-environment-model.md`

## Proposed Interface (synthesis)

Not a final design — a concrete starting point for `/team-plan` or `/create_plan` follow-up:

```python
# src/agent_on_demand/backends/protocol.py
from dataclasses import dataclass, field
from typing import BinaryIO, Protocol

@dataclass(frozen=True)
class PolicyRule:
    domain: str
    action: str  # "allow" | "deny"

@dataclass(frozen=True)
class NetworkPolicy:
    rules: list[PolicyRule]

class BackendError(Exception): ...
class SessionNotFoundError(BackendError): ...

@dataclass
class ExecutionError(BackendError):
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""

class WorkspaceFS(Protocol):
    def write_text(self, path: str, content: str) -> None: ...
    def chmod(self, path: str, mode: int) -> None: ...

class Command(Protocol):
    def set_output(self, stdout: BinaryIO, stderr: BinaryIO) -> None: ...
    def run(self) -> int: ...  # returns exit code; does NOT raise on non-zero

class SessionHandle(Protocol):
    @property
    def name(self) -> str: ...
    def workspace(self) -> WorkspaceFS: ...
    def make_command(self, *args: str, timeout: float | None = None) -> Command: ...
    def apply_network_policy(self, policy: NetworkPolicy) -> None: ...

class BackendClient(Protocol):
    def provision(self, name: str) -> SessionHandle: ...
    def get(self, name: str) -> SessionHandle: ...  # raises SessionNotFoundError
    def destroy(self, name: str) -> None: ...
    def close(self) -> None: ...

class Backend(Protocol):
    def create_client(self, token: str) -> BackendClient: ...
```

Then: `src/agent_on_demand/backends/sprites_backend.py` adapts `sprites-py==0.0.1-rc37` to this protocol, and `src/agent_on_demand/backends/fake_backend.py` provides an in-memory synchronous implementation for tests. `views.py`, `stream.py`, `signals.py`, and `admin.py` move from `SpritesClient`/`Sprite` to `BackendClient`/`SessionHandle`.

## Open Questions

1. **Client caching vs. per-request construction.** Should the `BackendClient` be cached per-user (fixes the `httpx.Client` leak but introduces thread-safety concerns) or created per-request with an explicit `close()` in a `finally` block? Track 2's resource-leak note doesn't prescribe a direction.
2. **Rename `session.sprite_name` to `session.backend_handle`?** This is a schema migration touching `models.py`, the `pre_delete` signal, admin bulk actions, and at least one e2e fixture. Worth doing as part of the abstraction, but may warrant a separate plan.
3. **Should `apply_network_policy` be idempotent?** Sprites semantics are unclear — the research didn't surface whether calling it twice is safe. The abstraction contract should declare one way or the other.
4. **Does the abstraction subsume the `cleanup_fn` pattern** introduced by the session-based-execution plan? If `run_session_background` now operates on a `SessionHandle`, the cleanup path might move from a closure parameter to a `handle.destroy()` call. Needs design review.
5. **Does the half-wired network policy path** (`thoughts/plans/2026-04-17-network-isolation.md`) land before, during, or after this abstraction? Doing it during gives us a second backend-independent operation to design against; doing it before grandfathers more Sprites-specific code into the rewrite.
