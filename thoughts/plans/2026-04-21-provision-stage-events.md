# Provisioning Stage Events on the SSE Stream — Implementation Plan

Issue: [#77](https://github.com/ravi-hq/agent-on-demand/issues/77)

## Overview

Today `POST /sessions` returns `202` and the SSE stream stays silent for 20–30s while AoD creates the Sprite, clones repos, writes configs, and starts the runtime CLI. Clients can only show a generic "waiting" spinner — they have no visibility into which stage is running or how long it's taken.

This plan emits lightweight stage events onto the existing SSE stream so clients can render live progress (e.g. `⠋ cloning ravi-hq/fairy · 4s`) without changing the session state machine or breaking existing consumers.

## Current State Analysis

- `provision_session_task` (at [`session_service/tasks.py:112`](/src/agent_on_demand/session_service/tasks.py#L112)) runs in a Procrastinate worker and calls `provision_session(user, spec)`.
- `provision_session` (at [`session_service/provisioning.py:38`](/src/agent_on_demand/session_service/provisioning.py#L38)) runs seven setup stages sequentially between `create_sprite` and the chained `execute_turn` task.
- Provisioning writes **nothing** to `AgentSessionLog` today — only `execute_turn` writes logs, and only after it flips the session to `running`.
- `stream.py::stream_session_from_db` (at [`stream.py:15`](/src/agent_on_demand/stream.py#L15)) tails `AgentSessionLog` and yields `start` / `output` / `turn_start` / `exit` / `error` / `terminated` / `stale` SSE events.
- `AgentSessionLog.stream` is a `CharField(max_length=6)` with choices `stdout`/`stderr` — fits new values only if we expand the enum.
- Existing stage labels live in `ProvisionError(stage=...)` already: `create`, `network_policy`, `env_file`, `packages.{manager}`, `clone`, `user_setup`, `mcp_config`, `skills`.
- Per-stage timing is already recorded as OTel span attributes (`session.provision` span, `aod.failure_stage` on failure), but not exposed in the API.

## Desired End State

1. During provisioning the SSE stream emits events like:
   ```
   data: {"type":"stage","id":12,"stage":"create_sprite","state":"started"}
   data: {"type":"stage","id":13,"stage":"create_sprite","state":"done","duration_ms":15200}
   data: {"type":"stage","id":14,"stage":"clone_repos","state":"started"}
   ...
   data: {"type":"stage","id":22,"stage":"runtime_start","state":"started"}
   data: {"type":"output","id":23,"stream":"stdout","data":"...","turn":1}
   ```
2. Skipped stages (empty packages, no setup_script, etc.) emit no events — absence = not run.
3. Every stage event carries an `id` consistent with existing `output` event ids, so `Last-Event-ID` resume keeps working.
4. Existing SSE event types are unchanged; no breaking changes for clients that only care about `output`/`exit`/`error`/`terminated`/`stale`.
5. The session status state machine is unchanged (still `pending → running → ...`).
6. `site/docs/api/streaming.md` documents the new type.
7. Unit tests cover provisioning-side emission and stream-side translation. One e2e test confirms real sessions emit the stages.

## What We're NOT Doing

- **Not adding a new status value.** Sub-state is carried by events, not by `session.status`. Clients that check `status in ("pending", "running", ...)` keep working.
- **Not emitting a `done` event for `runtime_start`.** The existing `exit`/`error`/`terminated` events already mark runtime completion; duplicating would be noise. (Flagged in Open Questions.)
- **Not splitting events across tables.** Stage events live in `AgentSessionLog` alongside `output` rows so one id sequence stays canonical (see Decisions Locked).
- **Not plumbing stage events through `POST /sessions/{id}/prompt`.** Continuations skip provisioning — only the `runtime_start` event fires, which falls out naturally.
- **Not exposing stage events anywhere besides the stream.** `GET /sessions/{id}` and `GET /sessions/{id}/turns` stay unchanged.
- **Not touching OTel spans or PostHog events.** Existing telemetry remains authoritative for internal observability.

## Decisions Locked

1. **Expand `AgentSessionLog`, don't create a new table.** A new `SessionEvent` model would split the id sequence, which breaks SSE `Last-Event-ID` resume (clients pass a single integer; we'd need to encode two cursors). Expanding the existing table keeps resume trivial. Cost: three new nullable columns.
2. **Stage events use `kind="stage"` on `AgentSessionLog`.** New `kind` field with choices `output | stage`. Keeps type discrimination explicit rather than overloading `stream` (which stays stdout/stderr for output rows).
3. **Unify stage names across `ProvisionError.stage` and stage events, in this same PR.** Current tags like `create`/`clone` get renamed to `create_sprite`/`clone_repos` to match the stage event schema. Single source of truth = module-level `STAGE_*` constants in `provisioning.py`. Changes an error-body string contract but no test currently asserts it verbatim (verify before locking).
4. **Provisioning stages emit `started`/`done` pairs.** `done` carries `duration_ms`. Clients need `started` to show "currently cloning" (not just past-tense "cloned"), and they need `done` to stop the per-stage timer cleanly. Sum of events per successful session: ~10–14 (small).
5. **`runtime_start` emits `started` only.** `done` would be redundant with the existing `exit` terminal event, and the per-chunk `output` events already tell clients the runtime is live.
6. **Failed stages emit `state="failed"` with `duration_ms` and a short `message`.** Fires from the `stage_timer` context manager's exception path, before `_mark_provision_failed` writes the session-level failure. Gives clients a per-stage reason on top of the session-level terminal.

## Implementation Approach

Four phases, each independently landable. Phase 1 adds the plumbing; Phase 2 emits events; Phase 3 surfaces them on the stream; Phase 4 documents + tests e2e.

## File Ownership Map

| Layer | File | Change |
|---|---|---|
| Model | `src/agent_on_demand/models/sessions.py` | Add `kind`, `stage`, `state`, `duration_ms` nullable fields to `AgentSessionLog`. |
| Migration | `src/agent_on_demand/migrations/0014_agentsessionlog_stage_fields.py` | Auto-generated, nullable columns only. |
| Provisioning | `src/agent_on_demand/session_service/provisioning.py` | Add `STAGE_*` constants, `emit_stage_event()` helper, `stage_timer()` context manager. Wrap each stage helper. |
| Task wiring | `src/agent_on_demand/session_service/tasks.py` | Thread `session_id` into `provision_session`. Emit `runtime_start` just before `sprite.command().run()` inside `_execute_turn_body`. |
| SSE | `src/agent_on_demand/stream.py` | Branch on `kind` — yield `stage` vs `output` events; include new fields in `.values()`. |
| Docs | `site/docs/api/streaming.md` | Document `stage` event type; add to event table, add a short replay/ordering note. |
| Unit tests | `tests/test_stream.py` (new or existing) | Stage rows translate into stage SSE events in id order. |
| Unit tests | `tests/test_session_service.py` (new or existing) | Provisioning stages emit expected events in expected order; skipped stages emit nothing; failed stages emit a `failed` event. |
| E2E | `tests/e2e/test_sessions.py::TestStreaming` | One new test: a real session emits a `create_sprite` → `runtime_start` stage sequence before the first `output`. |

## Phase 1: Schema + helper plumbing

**Goal:** `AgentSessionLog` can hold stage rows; provisioning has a clean API for emitting them. No events fired yet.

1. Edit `models/sessions.py`:
   ```python
   class AgentSessionLog(models.Model):
       KIND_CHOICES = [("output", "output"), ("stage", "stage")]
       STATE_CHOICES = [("started", "started"), ("done", "done"), ("failed", "failed")]
       ...
       kind = models.CharField(max_length=8, choices=KIND_CHOICES, default="output")
       stage = models.CharField(max_length=32, blank=True, default="")
       state = models.CharField(max_length=16, blank=True, default="")
       duration_ms = models.PositiveIntegerField(null=True, blank=True)
       # existing fields unchanged
       stream = models.CharField(max_length=6, choices=STREAM_CHOICES, blank=True, default="")
       data = models.TextField(blank=True, default="")
   ```
   Note: `stream` and `data` become `blank=True` since stage rows don't use them. Existing rows default `kind="output"` on migration.

2. Generate migration:
   ```bash
   uv run python manage.py makemigrations agent_on_demand
   ```
   Expected: pure additive, no backfill, no long-running ops.

3. Add to `session_service/provisioning.py`:
   ```python
   STAGE_CREATE_SPRITE = "create_sprite"
   STAGE_NETWORK_POLICY = "network_policy"
   STAGE_ENV_FILE = "env_file"
   STAGE_PACKAGES = "packages"  # + .{manager} suffix added at call site
   STAGE_CLONE_REPOS = "clone_repos"
   STAGE_USER_SETUP = "user_setup"
   STAGE_MCP_CONFIG = "mcp_config"
   STAGE_SKILLS = "skills"
   STAGE_RUNTIME_START = "runtime_start"

   def emit_stage_event(
       session_id: str,
       stage: str,
       state: str,
       duration_ms: int | None = None,
       message: str = "",
   ) -> None:
       from agent_on_demand.models import AgentSessionLog
       AgentSessionLog.objects.create(
           session_id=session_id,
           kind="stage",
           stage=stage,
           state=state,
           duration_ms=duration_ms,
           data=message,  # reused on failure only; empty for started/done
       )

   @contextlib.contextmanager
   def stage_timer(session_id: str, stage: str):
       emit_stage_event(session_id, stage, "started")
       start = time.monotonic()
       try:
           yield
       except Exception as e:
           emit_stage_event(
               session_id, stage, "failed",
               int((time.monotonic() - start) * 1000),
               message=str(e),
           )
           raise
       else:
           emit_stage_event(
               session_id, stage, "done",
               int((time.monotonic() - start) * 1000),
           )
   ```
   Stage rows reuse the existing `data` TextField for the optional failure message — `data` is empty for output-stage rows by design, so no column conflict and no extra migration field.

4. Update `ProvisionError.stage` tags to the new names (`create` → `create_sprite`, `clone` → `clone_repos`). Grep for any test asserting the old tag values before renaming.

Tests: none yet — plumbing only.

## Phase 2: Emit stage events during provisioning

**Goal:** Provisioning writes stage rows. Nothing reads them yet.

1. Change `provision_session` signature to take `session_id`:
   ```python
   def provision_session(user, spec: SessionSpec, session_id: str) -> Sprite:
   ```
   Update the one caller in `tasks.py::_provision_session_inner`.

2. Wrap every non-skipped stage:
   ```python
   with stage_timer(session_id, STAGE_CREATE_SPRITE):
       sprite = client.create_sprite(spec.name)
   ```
   And similarly for each stage helper. Skip-guards stay in place — a skipped stage emits nothing.

   For `_install_packages`, the stage name is `packages.{manager}` (one event pair per manager that actually had packages).

3. In `tasks.py::_execute_turn_body`, emit `runtime_start` just before `cmd_thread.start()`:
   ```python
   emit_stage_event(str(session.id), STAGE_RUNTIME_START, "started")
   cmd_thread = threading.Thread(target=_run_command, daemon=True)
   cmd_thread.start()
   ```
   No `done` event (see Decisions Locked #5).

4. Make sure `_mark_provision_failed` still runs after the `stage_timer` caught the exception and emitted a `failed` event, so clients get both the per-stage signal and the session-level `failed` terminal.

**Tests:**
- Mock the Sprites client; assert provision flow emits exactly the expected stage sequence for a representative config.
- Skipped stages produce no row.
- A failing stage emits one `started` and one `failed`, then the session is marked `failed`.

## Phase 3: Translate stage rows into SSE events

**Goal:** SSE clients see `stage` events interleaved with `output` events.

1. Update `stream.py::stream_session_from_db`:
   - Add `kind`, `stage`, `state`, `duration_ms` to the `.values(...)` projection.
   - Branch on `kind` inside the chunk loop:
     ```python
     for chunk in chunks:
         last_id = chunk["id"]
         if chunk["kind"] == "stage":
             payload = {"stage": chunk["stage"], "state": chunk["state"]}
             if chunk["duration_ms"] is not None:
                 payload["duration_ms"] = chunk["duration_ms"]
             if chunk["state"] == "failed" and chunk["data"]:
                 payload["message"] = chunk["data"]
             yield _format("stage", chunk["id"], payload)
         else:
             # existing turn_start + output handling, unchanged
     ```
   - Stage rows have no turn association; the existing `turn_start` emission only fires when `turn_id` changes, so stage rows don't trigger spurious `turn_start` events (they have `turn_id = NULL`, which is `!= last_turn_id` only on the first stage row — guard with `turn_id is not None`).

2. Add the guard to the existing `turn_id is not None and turn_id != last_turn_id` check.

**Tests:**
- Seed `AgentSessionLog` with a mix of stage and output rows; assert the generator yields stage events in id order, and `turn_start` only fires for real turn transitions.

## Phase 4: Docs + e2e

1. `site/docs/api/streaming.md` — add row to event table:

   | Type | Payload | Notes |
   |------|---------|-------|
   | `stage` | `{"type":"stage","id":N,"stage":"create_sprite","state":"started","duration_ms":15200}` | Emitted during provisioning and at runtime start. `duration_ms` present on `done`/`failed` only. Terminal events are still `exit`/`error`/`terminated`/`stale`. |

   Plus a short subsection on stage ordering (guaranteed by id) and the set of possible `stage` values.

2. New e2e test in `tests/e2e/test_sessions.py`:
   ```python
   @pytest.mark.slow
   def test_session_emits_provision_stage_events(create_session):
       session = create_session(...)
       events = [e for e in stream_events(session["id"]) if e["type"] == "stage"]
       stage_names = [e["stage"] for e in events if e["state"] == "started"]
       assert "create_sprite" in stage_names
       assert "runtime_start" in stage_names
       # don't assert the full list — optional stages depend on config
   ```

## Risks

- **Lock contention on `AgentSessionLog` inserts.** Provisioning now writes ~10 rows before the first `output`. Each is a small INSERT. Not a concern at current session volume; flag for review if per-session stage counts grow.
- **Stale `stage="started"` rows on crash.** If the worker is SIGKILL'd between a `started` and `done` emission, clients see a stuck stage. Mitigation: `_mark_provision_failed` emits a session-level `failed` terminal, which the CLI already interprets as "done, something broke." Clients should treat the session-level terminal as authoritative over any per-stage state.
- **`duration_ms` overflow.** `PositiveIntegerField` caps at 2,147,483,647 ms (~24 days). Any individual stage exceeding that has bigger problems; safe enough.
- **Stage-name stability.** Once documented in `streaming.md`, the set of `stage` values becomes a de facto API contract. Renames require a docs update + deprecation cycle.

## Verification

After Phase 4 lands:
1. Run `make test` — unit tests pass.
2. Run `make test-e2e-fast` — e2e (minus slow) pass.
3. Run the single new slow test with a token:
   ```bash
   AOD_API_TOKEN=... uv run pytest tests/e2e/test_sessions.py::test_session_emits_provision_stage_events -v
   ```
4. Follow-up on issue [#77](https://github.com/ravi-hq/agent-on-demand/issues/77): close on merge, reference in [#75](https://github.com/ravi-hq/agent-on-demand/issues/75) (pre-warmed pool) so the stage-event UX is usable once cold-start drops.
5. Update `examples/cli/example-cli.py` in a follow-up PR to consume the new `stage` event in its spinner ("⠋ cloning repos · 4s") — out of scope here, but wire-ready.

## Related

- Issue: [#77 — Emit provisioning stage events on the SSE stream](https://github.com/ravi-hq/agent-on-demand/issues/77)
- Paired: [#75 — Pre-warmed Sprite pool](https://github.com/ravi-hq/agent-on-demand/issues/75) (stage events become load-bearing UX once cold-start drops)
- Example consumer: `examples/cli/example-cli.py` from PR [#73](https://github.com/ravi-hq/agent-on-demand/pull/73) — the stderr spinner currently shows a single opaque "preparing sandbox" message.
