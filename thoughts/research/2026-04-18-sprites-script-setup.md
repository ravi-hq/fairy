---
date: 2026-04-18T20:35:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 703aeae7d5c13de73ced304bd7f51ba1314ba8bc
branch: main
repository: ravi-hq/agent-on-demand
topic: "How we run the agent on a Sprite: the on-box script vs. Django-side setup, and how debuggable that seam is"
tags: [research, team-research, sprites, sessions, turns, debugging]
status: complete
method: agent-team
team_size: 4
tracks: [script-anatomy, django-setup, turn-boundary, debug-surface]
last_updated: 2026-04-18
last_updated_by: Claude Code
---

# Research: How we run the agent on a Sprite

**Date**: 2026-04-18T20:35-07:00
**Researcher**: Claude Code (team-research)
**Git Commit**: [`703aeae`](https://github.com/ravi-hq/agent-on-demand/commit/703aeae7d5c13de73ced304bd7f51ba1314ba8bc)
**Branch**: `main`
**Repository**: ravi-hq/agent-on-demand
**Method**: Agent team (4 specialist researchers)

## Research Question

"I feel like the thinking around what we do in Sprites and how that maps to sessions and turns has gotten muddied. I want to dig deep and map out what we do currently — specifically the shell script we write on the box to run the agent vs. the setup we do, and how difficult it is to debug."

## Summary

The current model: **one shell script is generated per session and written once to the Sprite, then invoked per turn with a `run` or `continue` argument.** Django owns the mode decision and all orchestration; the script is mode-agnostic and only dispatches between two runtime-CLI command templates. Non-idempotent setup (packages, clone, user `setup_script`) is gated on a `/tmp/aod-initialized` sentinel — not on mode — so it's resilient to re-invocations. Per-turn state is just a prompt file at `/tmp/aod-prompt.txt` that Django overwrites before each call.

Debuggability is the sharpest pain point. The script has no `trap`, no retries, no status files, no structured logging — only `set -euo pipefail` and raw stderr. Django captures stdout/stderr verbatim into `AgentSessionLog`, but **exception messages from non-`ExecError` failures are captured in a local variable and then discarded** (stream.py:83-84) — the operator sees `{"type":"error","message":"Session failed"}` with no cause anywhere. There is no server-side timeout enforcement, no failure-reason field on the model, and 502 provision failures are never logged unless cleanup *also* fails.

## Research Tracks

### Track 1: Script anatomy — `/run-agent.sh`
**Researcher**: script-researcher
**Scope**: `src/agent_on_demand/sprites_exec.py` only

Script body lives in `build_wrapper_script` and emits this structure (sprites_exec.py:327-368):

1. **Strictness** — `#!/bin/bash` + `set -euo pipefail` (sprites_exec.py:327-328). No `trap`.
2. **Baked-in credentials** — `export <API_KEY_VAR>=<quoted>` and optional `export AOD_SESSION_ID=<uuid>` (sprites_exec.py:331-332). All env vars are baked into the script text at session-create time via `shlex.quote`, never re-applied per-turn.
3. **Env var block** — `export K=V` per env var, sorted alphabetically (sprites_exec.py:56-63, emitted at 333).
4. **Mode + prompt read** — `MODE="${1:-run}"`; fatal exit `2` with message `"missing prompt file: /tmp/aod-prompt.txt"` if prompt file absent; otherwise `PROMPT=$(cat ...); export PROMPT` (sprites_exec.py:335-341).
5. **Working dir** — `cd /home/sprite` then unconditional `mkdir -p .gemini` (sprites_exec.py:343-344). `.gemini` is created even for non-Gemini runtimes.
6. **Gated one-time setup** — block gated on `[ ! -f /tmp/aod-initialized ]` (sprites_exec.py:347), containing:
   - git init if no `.git` (sprites_exec.py:348-352), swallowing errors
   - package install: apt → cargo → gem → go → npm → pip, all silenced (sprites_exec.py:66-90)
   - repo clone via temporary `/tmp/.git-credentials` (sprites_exec.py:100-129)
   - user `setup_script` emitted verbatim (sprites_exec.py:93-97)
   - final `touch /tmp/aod-initialized` (sprites_exec.py:356)
7. **MCP configs — always run** (sprites_exec.py:132-213, emitted at 359): Claude/OAuth → JSON at `/tmp/mcp.json`; Codex → TOML at `~/.codex/config.toml`; Gemini → JSON at `~/.gemini/settings.json`. All written via heredoc.
8. **Skills — always run** (sprites_exec.py:233-253, emitted at 361): one directory per skill, `SKILL.md` via single-quoted heredoc (no variable expansion).
9. **Agent dispatch** (sprites_exec.py:363-367):
   ```
   case "$MODE" in
       run)      exec {config.cmd}{mcp_flags} ;;
       continue) exec {config.continue_cmd}{mcp_flags} ;;
       *)        echo "unknown mode: $MODE" >&2; exit 2 ;;
   esac
   ```
   Prompt is passed via `$PROMPT` env var; runtime-specific templates in `runtimes.py:56-87`.

The docstring (sprites_exec.py:280-297) explicitly defends this design: script instead of `env=` because `env=` would (1) replace `PATH` and break binary lookup, (2) leak API keys into WebSocket URL query params.

### Track 2: Django-side setup
**Researcher**: setup-researcher
**Scope**: `session_service.py`, `views/sessions.py`, `crypto.py`, `runtimes.py`

**Session create** (`views/sessions.py:199-325`):
1. Pydantic-validate `RunRequest`; resolve agent + environment + runtime; reject archived resources.
2. `UserRuntimeKey.get_api_key()` decrypts the runtime API key; 400 if missing.
3. `runtime_session_id = str(uuid.uuid4())` pre-generated at views/sessions.py:245.
4. `build_wrapper_script(config, api_key, runtime_session_id, repos, environment, mcp_servers, skills)` — pure string, no I/O yet. Prompt of turn 1 is `agent.system + req.prompt` (views/sessions.py:249-251); subsequent turns don't prepend.
5. `provision_session` (session_service.py:70-106): creates the Sprite, applies `networking_type="limited"` policy if needed, writes `/run-agent.sh`, writes `/tmp/aod-prompt.txt`, chmods. Any `SpriteError` → best-effort cleanup (delete Sprite, warn-log) → re-raise as `ProvisionError` → 502.
6. Atomic DB write: `AgentSession(status="pending")` + `SessionTurn(turn_number=1, status="pending")` + `SessionResource` rows (views/sessions.py:295-300). Resource auth tokens encrypted at rest.
7. `start_turn(session, turn, sprite, mode="run", timeout)` spawns a daemon thread (session_service.py:145-158); HTTP returns 202 immediately.

**Continue turn** (`send_prompt`, views/sessions.py:367-447):
1. Pre-check `session.status ∉ {running, terminated}` (views/sessions.py:381-385).
2. Re-check under `select_for_update` (views/sessions.py:409-413).
3. Allocate `next_turn_number = MAX + 1` inside the locked transaction (views/sessions.py:415-423). Reset session to `status="pending", exit_code=None`.
4. `session.prompt = req.prompt` — overwrites the first-turn prompt (views/sessions.py:424).
5. Commit transaction, then `write_prompt(sprite, req.prompt)` overwrites `/tmp/aod-prompt.txt` (session_service.py:118-124). **If this fails, the turn row is orphaned** (no rollback — see Muddy Zone 3).
6. `start_turn(mode="continue")` spawns daemon thread.

**Key boundaries**:
- Script written once at session-create, never re-uploaded.
- Only the prompt file changes between turns.
- All Sprites SDK coupling lives in `session_service.py`; views see typed exceptions.
- `destroy_session` (session_service.py:127-142) never raises — failures are warn-logged.

### Track 3: Turn-boundary seam
**Researcher**: turn-boundary-researcher
**Scope**: cross-cutting — models, views, session_service, script dispatch

**Django owns the mode decision entirely.** The script is mode-agnostic; `$1` selects between `config.cmd` and `config.continue_cmd` (sprites_exec.py:363-365).
- `_create_session` → `start_turn(..., "run")` — always turn 1 (views/sessions.py:313).
- `send_prompt` → `start_turn(..., "continue")` — always turn 2+ (views/sessions.py:437).

**Per-runtime continuation strategy** (runtimes.py:56-87):
- **Claude / claude-oauth**: turn 1 uses `--session-id "$AOD_SESSION_ID"`; continue uses `--resume "$AOD_SESSION_ID"`. The UUID is pre-generated by Django (views/sessions.py:245), stored on `AgentSession.runtime_session_id` (migration 0012), and baked into the script as `export AOD_SESSION_ID=…`. Runtimes.py:57-60 comment explicitly explains this was chosen over `--continue` in `--print` mode, which was observed to silently fork new sessions.
- **Codex**: turn 1 pipes stdin; continue uses `codex exec resume --last`.
- **Gemini**: turn 1 uses `-p`; continue adds `--resume`.

**Data model** (migrations 0011, 0012):
- `SessionTurn` with `(session, turn_number)` unique. Migration 0011 backfilled a synthetic turn 1 for existing sessions.
- `AgentSession.runtime_session_id` added in 0012.

**Handoff contract**:
- **Django → Script**: `/run-agent.sh` (written once), `/tmp/aod-prompt.txt` (rewritten each turn), `$1` = `"run"` | `"continue"`, baked-in `AOD_SESSION_ID` and API key.
- **Script → Django**: exit code (via `ExecError` or `cmd.run()` return), streamed stdout/stderr captured as `AgentSessionLog` rows tagged with the current `SessionTurn`.

### Track 4: Debug surface
**Researcher**: debug-researcher
**Scope**: `stream.py`, error paths in `sprites_exec.py` / `session_service.py` / views

**What you have on a failure:**
- `AgentSession.status`, `exit_code` (nullable), `sprite_name`
- `SessionTurn.status`, `exit_code`, `started_at`, `ended_at`, `prompt`
- `AgentSessionLog` — every stdout/stderr byte from the script, tagged by stream and turn

No dedicated `error_message`, `traceback`, or structured-failure field exists on any model.

**Failure mode × observability matrix:**

| Failure | User sees | Logged | Persisted | Gap |
|---|---|---|---|---|
| Script exits nonzero | SSE `{"type":"exit","code":N}` | Stderr in `AgentSessionLog` | `status=failed, exit_code=N` | No structured cause — must grep stderr |
| Missing prompt file | SSE `{"type":"exit","code":2}` | `"missing prompt file: …"` in stderr | `exit_code=2` | Indistinguishable from any other `exit 2` |
| Non-`ExecError` exception (network, WS drop) | SSE `{"type":"error","message":"Session failed"}` | **Nothing** | `status=failed, exit_code=None` | **Exception string discarded — stream.py:83-84** |
| Provision failure | HTTP 502 | Nothing (unless cleanup also fails) | Nothing — no session row created | If caller doesn't log the 502 body, no trace |
| External terminate mid-run | SSE `{"type":"terminated"}` | Nothing | session `terminated`; turn `failed` with whatever exit_code | Turn marked "failed" on a clean operator-initiated terminate |
| Hung session | Heartbeats every 15s, no exit event | Nothing | `status=running` indefinitely | No server-side timeout enforcement in Django |
| Destroy fails after terminate | 200 OK, `status=terminated` | `WARNING Failed to cleanup Sprite` | `sprite_name=""` | Sprite may still bill; no retry handle |
| Corrupt runtime-key ciphertext | HTTP 500 | Django's default 500 handler only | Nothing | No structured decryption-failure signal |
| Package install fails (apt/pip/npm etc.) | SSE exit N | Whatever the package manager emitted — but installs run `-qq` / `--silent`, so stderr may be almost empty | `exit_code=N` | Absence of stderr + nonzero exit is itself the signal |
| Clone fails mid-crash | SSE exit N | Git's stderr (not silenced) | `exit_code=N` | **`/tmp/.git-credentials` leaks on crash** — no `trap` to unlink |
| User `setup_script` fails | SSE exit N | Whatever the user's commands emit | `exit_code=N` | No wrapping label — stderr looks identical to "package install failed" |
| Hung thread + failed state race (Zone 8) | SSE `{"type":"error","message":"Session failed"}`, then re-runnable | Nothing | `status=failed, exit_code=None` | Sprite still executing; second turn can start on same Sprite |

### Debug cookbook (what an operator does today)
1. `GET /sessions/{id}` — if `exit_code=None` and `status=failed`, failure mode 3 — no server-side cause anywhere.
2. `GET /sessions/{id}/stream` or query `AgentSessionLog` by session_id — filter `stream='stderr'` for bash-side errors.
3. `GET /sessions/{id}/turns` — per-turn timing/exit_code to isolate which turn failed.
4. For provision failures: check Django stdout for `WARNING Failed to cleanup Sprite`; otherwise no trace.
5. For hung sessions: `POST /sessions/{id}/terminate` is the only recourse.

## Cross-Track Discoveries

**1. The script is write-once, but setup is sentinel-gated — mode is orthogonal to setup.**
Mode (`run` vs `continue`) only changes the *last line* (which CLI invocation). All provisioning work — packages, clone, user setup_script, git init — is gated on `/tmp/aod-initialized`, not on mode. This means a `continue` invocation that somehow lands before a successful `run` will still perform first-time setup. Conversely, re-invoking `run` after completion does not repeat setup. Track 1 documents the gating; Track 3 confirms Django doesn't rely on mode for setup semantics.

**2. `runtime_session_id` lives in two places and is never re-read.**
The UUID is generated at session-create (views/sessions.py:245), stored on `AgentSession.runtime_session_id`, and *also* baked into the script text as `export AOD_SESSION_ID=…` (sprites_exec.py:323-324). Nothing reads it back from the DB — the authoritative copy is on the Sprite. If the script were ever regenerated or the Sprite were replaced, the DB field and on-box value could diverge. Currently this can't happen because the script is write-once.

**3. Turn-boundary decisions are "Django tells, script obeys" — the seam itself is shallow.**
There is no ambiguity about who decides what. Django picks the mode, writes the prompt file, and calls the script. The script does no session-level bookkeeping. The *muddiness* is in the neighborhood of the seam — orphan turns, dropped exceptions, silent per-turn side effects — not in the seam itself.

**4. The biggest debuggability hole is a single `except Exception as e` that drops `str(e)` on the floor.**
stream.py:83-84: `except Exception as e: result_holder.append(("error", str(e)))`. The string is then used only to decide `final_status = "failed"` and `exit_code = None` (stream.py:128-133), and is never logged or persisted. Anything that isn't a clean `ExecError` — WebSocket drop, network error, Sprites SDK bug — becomes an untraceable "Session failed".

## Muddy Zones

These are places where the design is not obviously wrong but where the responsibility is unclear or the behavior is surprising — the natural places to push back on. Zones 1–5 are from Track 3's initial pass; 6–8 were surfaced via cross-researcher verification after initial findings.

**Muddy Zone 1 — ~~Double status check without lock~~ — RESOLVED on cross-check.**
`send_prompt` reads `session.status` twice: once before (views/sessions.py:381-385) and once inside `select_for_update` (views/sessions.py:409-413). On closer inspection with `setup-researcher`, this is a safe optimistic-lock fast-path pattern: the pre-lock check is a fast 409 exit, the inner check is authoritative. No real race. Keeping here for the record.

**Muddy Zone 2 — `failed` and `completed` sessions can resume — confirmed intentional.**
`send_prompt` blocks only on `running` and `terminated`. A `failed` session can receive a new prompt and will invoke `bash /run-agent.sh continue`. Whether the runtime CLI can gracefully `--resume` a previously-failed session is runtime-specific; there's no Django-level guard. Confirmed as intentional per CLAUDE.md ("non-running, non-terminated") — but whether that's actually *right* is a taste call worth pushing back on, especially combined with Muddy Zone 8 below.

**Muddy Zone 3 — `write_prompt` failure orphans turn rows.**
In `send_prompt`, the DB transaction commits the new turn row *before* `write_prompt` runs (views/sessions.py:433-437). If `write_prompt` raises `ProvisionError`, the 502 is returned but the turn row remains in `pending` forever, never transitions, never runs. The session itself is also left in `pending`, so a retry creates turn N+2 alongside the orphan. Independently confirmed by both Track 2 and Track 3.

**Muddy Zone 4 — `runtime_session_id` has two storage locations that could diverge — and it's Claude-specific.**
`AOD_SESSION_ID` is Claude-only: Claude's `--session-id` / `--resume` uses it directly (runtimes.py:63-64, 83-84). Codex and Gemini don't reference `AOD_SESSION_ID` at all — Codex uses `--last`, Gemini uses bare `--resume`. So the DB-vs-script divergence risk is specifically a Claude correctness risk. Not exploitable today because the script is write-once, but it's load-bearing and implicit.

**Muddy Zone 5 — ~~`AgentSession.prompt` is overwritten per turn~~ — downgraded.**
`send_prompt` sets `session.prompt = req.prompt` (views/sessions.py:424), so the field no longer matches the turn-1 prompt on multi-turn sessions. Not exposed in API (`_serialize_session` omits it) and not read by any current view or service path, so practically harmless. A denormalization wart, not a live bug.

**Muddy Zone 6 — System prompt carry-forward is an implicit runtime assumption. (NEW)**
`agent.system` is prepended to the prompt only on turn 1 (views/sessions.py:249-251): `effective_prompt = f"{agent_obj.system}\n\n{req.prompt}"`. On turn 2+, Django sends only `req.prompt`. Django trusts the runtime CLI's `--resume` mechanism to carry the system context forward from runtime-side session state. If that state is lost (Sprite process restart, runtime bug, expired session store), turn 2+ runs *without* the agent's system prompt and Django has no way to detect this — the turn would complete normally, just with different behavior.

**Muddy Zone 7 — Claude `continue` silently adds `--dangerously-skip-permissions`. (NEW)**
Claude's `run` cmd: `claude --print --verbose --output-format stream-json --session-id "$AOD_SESSION_ID" -p "$PROMPT"`. Claude's `continue` cmd: `claude --dangerously-skip-permissions --print --verbose --output-format stream-json --resume "$AOD_SESSION_ID" -p "$PROMPT"` (runtimes.py:62-65). The permission-bypass flag is present on `continue` and absent on `run`. Every turn after the first runs with elevated permissions that turn 1 didn't have. Undocumented, not exposed, not surfaced to callers. Presumably added to prevent interactive prompts from blocking the resume — but the security model diverges silently between turn 1 and turn N.

**Muddy Zone 8 — `cmd_thread.join(timeout=5.0)` creates a zombie-execution window. (NEW, serious)**
stream.py:121: `cmd_thread.join(timeout=5.0)`. If the thread is genuinely stuck (Sprites WebSocket hung, network partition), join returns after 5s with `result_holder` empty. Code falls through to `final_status = "failed", exit_code = None` (stream.py:131-133). Session is marked `failed` in DB. The daemon thread is still alive and the Sprite is still executing `bash /run-agent.sh`. Combined with Muddy Zone 2 (`failed` is resumable), a client can immediately `POST /prompt` on the still-failed session, allocating turn N+1 and spawning a *second* execution on the *same Sprite*. Two threads now write to `AgentSessionLog` for the same sprite, interleaved, tagged with different turn FKs. There is no mechanism to kill the on-Sprite process from Django — it runs until it exits naturally or the Sprite itself times out. This is the single most concerning interaction surfaced by the research.

## Code References

| File | Tracks | What's here |
|---|---|---|
| `src/agent_on_demand/sprites_exec.py:327-368` | 1, 3 | Wrapper script template |
| `src/agent_on_demand/sprites_exec.py:347-357` | 1, 3 | `/tmp/aod-initialized` sentinel gate |
| `src/agent_on_demand/sprites_exec.py:363-365` | 1, 3 | `run`/`continue` case dispatch |
| `src/agent_on_demand/sprites_exec.py:280-297` | 1 | Design-decision docstring |
| `src/agent_on_demand/runtimes.py:56-87` | 1, 3 | Per-runtime `cmd` / `continue_cmd` |
| `src/agent_on_demand/session_service.py:70-106` | 2 | `provision_session` — Sprite create + script upload |
| `src/agent_on_demand/session_service.py:118-124` | 2, 3 | `write_prompt` (per-turn state update) |
| `src/agent_on_demand/session_service.py:127-142` | 2, 4 | `destroy_session` — swallows cleanup failures |
| `src/agent_on_demand/session_service.py:145-158` | 2, 3 | `start_turn` — daemon-thread launch |
| `src/agent_on_demand/views/sessions.py:245` | 2, 3 | `runtime_session_id = uuid.uuid4()` |
| `src/agent_on_demand/views/sessions.py:249-251` | 2 | Turn-1-only `agent.system` prepend |
| `src/agent_on_demand/views/sessions.py:295-300` | 2 | DB write of session + turn + resources |
| `src/agent_on_demand/views/sessions.py:367-447` | 2, 3 | `send_prompt` flow |
| `src/agent_on_demand/views/sessions.py:381-385` | 3 | Outer status check (pre-lock) |
| `src/agent_on_demand/views/sessions.py:409-423` | 3 | Locked re-check + turn allocation |
| `src/agent_on_demand/views/sessions.py:424` | 3 | `session.prompt` overwrite |
| `src/agent_on_demand/views/sessions.py:433-437` | 3 | Orphan-turn race window |
| `src/agent_on_demand/stream.py:60-147` | 3, 4 | `run_session_background` — thread body + final status write |
| `src/agent_on_demand/stream.py:75` | 3, 4 | `sprite.command("bash", "/run-agent.sh", mode, ...)` |
| `src/agent_on_demand/stream.py:80-84` | 4 | `ExecError` branch + dropped `Exception` message |
| `src/agent_on_demand/stream.py:137-141` | 4 | Guard that preserves `terminated` status |
| `src/agent_on_demand/stream.py:186-192` | 4 | The three final-event SSE shapes |
| `src/agent_on_demand/migrations/0011_add_session_turns.py` | 3 | `SessionTurn` table + backfill |
| `src/agent_on_demand/migrations/0012_add_runtime_session_id.py` | 3 | `runtime_session_id` field |

## Architecture Insights

- **Script generation is a pure string operation.** No I/O during `build_wrapper_script`; all secrets and IDs are baked in via `shlex.quote`. This keeps the unit-testable boundary clean.
- **The wrapper-script approach is deliberate** (sprites_exec.py:294-297) — using `env=` on `sprite.command` was rejected because it would clobber `PATH` and leak API keys into WebSocket URL query params.
- **Turn-level bookkeeping is entirely Django's job.** The script has no awareness of turns; it runs the runtime CLI and exits. Turn numbering, state transitions, and streaming-to-turn association all live on the Django side.
- **Sentinel-gated setup is idempotent by file existence**, not by mode. This is robust against re-invocations but means setup cannot be *forced* to re-run without manually deleting `/tmp/aod-initialized`.
- **Daemon thread for execution** (stream.py:95-96) — HTTP returns 202 immediately; the `run_session_background` thread holds the Sprite command until completion. All per-thread state lives in `result_holder`, `output_q`, `db_buffer` locals; nothing is shared.

## Open Questions (for the pushback round)

The researchers kept this descriptive, but these are the places the team flagged as most likely targets for "is this right?":

1. **Is baking the API key into a file on disk the right trade-off?** The design-decision docstring explains why not `env=`, but it doesn't discuss e.g. writing the key to an in-memory-only channel. The key sits in `/run-agent.sh` on the Sprite filesystem for the lifetime of the session.
2. **Should the script have any failure telemetry of its own?** Today, if `apt-get install` fails, we learn about it only by grepping stderr in `AgentSessionLog`. A structured `/tmp/aod-stage-failed` file, or a `trap ERR` that reports the failing stage, would be cheap.
3. **Should non-`ExecError` exceptions be logged or persisted?** One `logger.exception(...)` in stream.py:83-84 would fix the worst debug blind spot.
4. **Should `failed` sessions be resumable?** If not, guarding `send_prompt` is one line.
5. **Should the script be mode-agnostic at all?** An alternative: Django writes the full CLI invocation (including args) into a per-turn file, and the script just `exec`s whatever's in it. That moves more logic to Django but makes the script stupid and the contract explicit.
6. **Should Django enforce a server-side timeout** independent of the Sprites-client timeout? Today a hung session stays `running` forever from Django's perspective.
7. **Should turn creation and `write_prompt` be in the same transactional boundary?** As-is, a failed prompt write orphans a turn row (Muddy Zone 3).
8. **Should the script have a `trap ERR` to emit a structured "stage failed" line** (and clean up `/tmp/.git-credentials`)? Today, a silent-flag apt failure leaves almost nothing in stderr, and a clone crash leaks the token.
9. **Should `--dangerously-skip-permissions` apply symmetrically to turn 1?** Either both or neither — the current asymmetry is surprising.
10. **Should Django kill or abandon the background thread deterministically?** Today, `cmd_thread.join(timeout=5.0)` creates the zombie window in Muddy Zone 8; either extend the wait, propagate timeout to a Sprite-side kill, or guard `send_prompt` against `failed` states created by join-timeout.
11. **Should the runtime be given a way to report back that its session state was lost?** Muddy Zone 6 (system prompt carry-forward) is undetectable today.

## Related Research

None found in `thoughts/research/`.
