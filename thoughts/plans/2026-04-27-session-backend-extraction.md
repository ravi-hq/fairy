# Session backend extraction

A sequenced PR plan for pulling Sprites out from behind a `Backend`
Protocol so alternative impls (Modal sandboxes, etc.) can plug in.

## Context

The interface design was done in
`thoughts/research/2026-04-18-session-backend-abstraction.md` —
seven-method Protocol, three exception classes, two sub-protocols
(`BackendClient` + `SessionHandle`, with `WorkspaceFS` under the
handle). That research stands; the recommendations there are still
load-bearing. Read it first.

Since that research, the codebase reshape has actually *helped* the
extraction:

- `views.py` → split into `views/{agents,environments,sessions,health}.py`.
- `stream.py` → deleted; SSE now tails `AgentSessionLog` directly.
- `signals.py` → gone; lifecycle moved into Procrastinate tasks.
- `session_service/` package was introduced as the explicit boundary
  (its `__init__.py:1-6` says "All coupling to Sprites lives here, so
  it can later be placed behind a Protocol without touching the call
  sites" — that's the door this plan walks through).
- `tests/fakes/sprite.py` exists as a recording fake with the shape
  the Protocol needs.

Current Sprites surface (11 files, ~50 call sites total):

| Module | What touches Sprites |
|---|---|
| `session_service/client.py` | `SpritesClient`, `SpriteError`, `client.delete_sprite` |
| `session_service/provisioning.py` | `Sprite`, `SpriteError`, `NetworkPolicy`, `PolicyRule`, `client.{create,get}_sprite`, `sprite.{filesystem,command,update_network_policy}` |
| `session_service/tasks.py` | `ExecError`, `cmd.stdin/stdout/stderr`, `cmd.run()` |
| `session_service/turn.py` | `Sprite` type ref only |
| `runtimes/{base,claude,codex,gemini,opencode}.py` | `Sprite` param; uses `.filesystem()` and `.command(...)` |
| `admin.py` | `SpritesClient`, `SpriteError`, bypasses `session_service` |
| `sprites_patch.py` | websocket close_timeout monkeypatch (init-time) |
| `models/sessions.py:34` | `sprite_name` DB column |
| `models/auth.py:91` | `UserSpritesKey` credential model |
| `views/sessions.py` | `session.sprite_name` reads/writes; user-facing "Sprites" error strings |
| `config/settings.py:114-115` | `SPRITES_BASE_URL` config |

That's the full ledger. The five files in `runtimes/` plus `turn.py`
are pure type-renames (they only call `.filesystem()` / `.command()`).
The real work is in the three `session_service/` modules, the schema
columns, and the credential model.

## Strategy

Same playbook as the session-service quality pass:

1. Protocol-first: introduce `Backend`/`BackendClient`/`SessionHandle`
   types in `session_service/backend.py`, with `SpritesBackend` as the
   only impl. No callers switch yet.
2. Move `session_service/` internals onto the Protocol. SpritesBackend
   stays the only impl; behavior is identical.
3. Move runtimes onto the Protocol. Five-file mechanical rename.
4. Move the execution path (`tasks.py::_execute_turn_body`) onto the
   Protocol. Threading core unchanged.
5. Neutralize the API surface: error strings, `admin.py` bulk action,
   `NoSpritesKeyError` → `NoBackendCredentialsError`.
6. Schema work: `backend` discriminator column, `sprite_name` →
   `backend_handle` rename, `UserSpritesKey` → `UserBackendCredential`.
   Each as its own additive-migration PR (forward-only — see
   `docs/runbook.md` and `MIGRATION_LINTER_OPTIONS`).

PRs 1–5 ship without any schema change or user-visible API change.
PRs 6–8 are the schema cluster and can land independently after.
A separate plan covers the actual `ModalBackend` impl + per-user
backend selection — out of scope here.

Each PR ≤300 net LOC. PRs 1–5 are independently revertable. The
schema PRs (6–8) are forward-only by repo policy and need to land in
order.

## PR sequence

### PR 1 — Introduce `Backend` Protocol + `SpritesBackend` adapter

**New files:**
- `session_service/backend.py` — Protocol definitions per the
  2026-04-18 research's "Proposed Interface" section (lines 218–268),
  modulo the two refinements below.
- `session_service/sprites_backend.py` — adapter class wrapping
  `sprites.SpritesClient` / `Sprite` / `SpriteFilesystem` / `Cmd` to
  the Protocol. Translates `SpriteError` → `BackendError`,
  `NotFoundError` → `SessionNotFoundError`, `ExecError` →
  `ExecutionError` (normalize `.exit_code` from method to attribute).
- `tests/fakes/backend.py` — `FakeBackend` impl built off the
  existing `tests/fakes/sprite.py` recording shape. Keep the old
  `RecordingSpritesClient` as a thin layer over `FakeBackend` so
  existing tests don't break.

**Refinements vs the research:**
- Drop `set_executable` from the Protocol (research §6 already
  recommends this; collapse into `WorkspaceFS.chmod`).
- `Command.run() -> int` returns the exit code directly (research §3,
  Cross-Track Discovery). Callers don't catch `ExecutionError` for
  exit codes; `ExecutionError` is reserved for transport-layer
  failures.

**No callers switch yet.** Protocol + impl ship dark.

**Tests:** Round-trip test that `SpritesBackend` exposed via the
Protocol still drives the existing `tests/fakes/sprite.py` recording
expectations. Verify error translation (Sprites → Backend
exceptions).

**Risk:** None — purely additive.
**Size:** ~250 LOC source (`backend.py` ~80, `sprites_backend.py`
~150, `fake backend.py` ~80), ~150 LOC tests.

### PR 2 — Move `session_service/` internals to the Protocol

**Refactor:**
- `session_service/client.py` — `get_client(user)` returns a
  `BackendClient`, not a `SpritesClient`. Internally still constructs
  `SpritesBackend(...).create_client(token)`.
- `session_service/provisioning.py` — replaces the
  `from sprites import ...` line with Protocol imports. The
  `_apply_network_policy` helper moves from `sprites.NetworkPolicy /
  PolicyRule` to neutral types re-exported from `backend.py`. The
  per-stage helpers (`_install_runtime`, `_write_env_file`, etc.)
  take `SessionHandle` instead of `Sprite`.
- `session_service/turn.py` — drop the `from sprites import Sprite`
  type-ref import.

**Why:** This collapses the "all Sprites coupling lives in
`session_service/`" promise from a docstring aspiration into a
load-bearing fact. After this, `grep -rn "from sprites" src/
session_service/` should return zero hits except `sprites_backend.py`.

**Tests:** Existing `test_session_service.py` (and the per-stage tests
landing in the quality-pass PR 6) keep passing without modification —
the fake conftest just swaps `RecordingSpritesClient` for
`FakeBackend`.

**Risk:** Light. Mechanical type swap. The stage-event names stay the
same (they're API contract via SSE — see `provisioning.py:46`).
**Size:** ~120 LOC net diff in `session_service/`, ~50 LOC in tests.

### PR 3 — Move runtimes to `SessionHandle`

**Refactor:** `runtimes/{base,claude,codex,gemini,opencode}.py`
swap `from sprites import Sprite` for `from session_service.backend
import SessionHandle`. The Protocol method signatures change from:

```python
def install(self, sprite: Sprite) -> None: ...
def write_config(self, sprite: Sprite, ...) -> None: ...
```

to:

```python
def install(self, handle: SessionHandle) -> None: ...
def write_config(self, handle: SessionHandle, ...) -> None: ...
```

The bodies change `sprite.filesystem()` → `handle.workspace()` and
`sprite.command(...).run()` → `handle.make_command(...).run()`.

**Why:** Runtimes are five files that each touch ~2 Sprite calls.
Mechanical rename, but it's the largest "leaf" of the dependency
graph and clears the path for PR 4.

**Tests:** Existing per-runtime tests pass with the fake conftest.
Add no-op type-coverage tests if mypy/pyright catches anything.

**Risk:** None — pure rename. The `Runtime` Protocol is internal.
**Size:** ~60 LOC net change in source, no new tests.

### PR 4 — Move `_execute_turn_body` to `SessionHandle`

**Refactor:** `tasks.py::_execute_turn_body` swaps the
`sprite.command(*argv, cwd=, timeout=)` + `cmd.stdin/stdout/stderr` +
`cmd.run()` + `except ExecError` block for the Protocol's
`Command.run() -> int` shape:

```python
cmd = handle.make_command(*argv, cwd="/home/sprite", timeout=timeout)
cmd.set_input(prompt.encode("utf-8"))
cmd.set_output(stdout=stdout_writer, stderr=stderr_writer)
exit_code = cmd.run()  # never raises on non-zero
result_holder.append(("exit", exit_code))
```

Note: the Protocol gains a `Command.set_input(bytes)` method since
the current code assigns `cmd.stdin = io.BytesIO(prompt.encode(...))`
and stdin is *not* in the 2026-04-18 research's interface. Update
`backend.py` from PR 1 with this addition (or, cleaner: make it a
constructor arg on `make_command`).

The `try/except ExecError` block in `_execute_turn_body` is gone —
exec errors return the exit code, transport errors raise
`BackendError` and are caught by the existing broad `except
Exception` path.

**Why:** This is the only place the Sprites SDK's
"`ExecError.exit_code()` is a method" wart leaks into app code
(`tasks.py:441`). Removing it eliminates the comment in `stream.py`
that the 2026-04-18 research called out (Cross-Track Discovery §3).

The threading core (`_run_command`, `output_q`, `TaggingQueueWriter`,
`_flush_buffer`) is unchanged — it's a producer-consumer over an
`io.BinaryIO` writer, which the Protocol preserves.

**Tests:** The existing `tests/test_tasks_*` tests need updating only
where they directly construct a `RecordingSprite` with `command`
expectations. Three or four call sites per the 2026-04-18 research
Track 3 finding §6.

**Risk:** Medium. This is the hot path for every turn. Lean on the
existing `make test` integration tests + a careful e2e run before
landing.
**Size:** ~80 LOC net change in `tasks.py`, ~60 LOC test diff.

### PR 5 — Neutralize the API + admin surface

**Refactor:**
- `admin.py` — bulk-terminate constructs `BackendClient` via
  `session_service.get_client(user)` instead of `SpritesClient(...)`
  inline (`admin.py:237-247`). Drops the `from sprites import` line.
- `session_service/errors.py` — rename `NoSpritesKeyError` →
  `NoBackendCredentialsError`. Keep `NoSpritesKeyError` as an
  alias for one release for back-compat with any external imports
  (none today, per grep — but cheap insurance).
- `views/sessions.py:232` — change `"No Sprites API key configured"`
  → `"No backend credentials configured"`. Same for the
  "Session sprite is no longer available" string at `:427` →
  "Session backend is no longer available".
- `views/sessions.py:88` — docstring `"Absolute path inside the
  Sprite where repo is cloned"` → `"Absolute path inside the
  workspace where repo is cloned"`.
- `sprites_patch.py` — move from a top-level module imported in
  `apps.py:18` into `SpritesBackend.__init__` (apply once, idempotent).

**Why:** This is the last user-visible "Sprites" vocabulary.
Everything below this PR is internal. After PR 5, an end user could
not tell from the API surface that Sprites is the underlying
backend.

**Tests:** Snapshot the renamed error strings; existing `test_*`
that asserted on the old strings need a one-line update.

**Risk:** API string changes are technically a breaking change for
SDK clients that grep on `detail`. The strings are not part of the
documented contract per `docs/openapi.yaml`, but it's worth a
heads-up in `CHANGELOG.md`.
**Size:** ~50 LOC source diff, ~30 LOC test diff.

### PR 6 — Add `backend` discriminator column on `AgentSession`

**Schema change:** Additive migration adds
`AgentSession.backend = CharField(max_length=32, default="sprites")`.
Migration is forward-only and backfill-safe (default value handles
existing rows).

**Code change:** `_build_spec_for_session` plumbs `session.backend`
into a `Backend` factory: `BACKENDS[session.backend].create_client(
token)`. Today there's only one entry: `BACKENDS = {"sprites":
SpritesBackend()}`. New sessions default to `"sprites"`; the column
is not exposed via the API surface yet (no `backend` field in
serializer schemas).

**Why:** This is the load-bearing column for ever supporting a
second backend. Adding it now (with one impl) lets PR 7 + PR 8 +
the eventual Modal PR each land independently without coordinating.

**Tests:** Migration test (existing `tests/test_migrations.py`
patterns), plus `_build_spec_for_session` tests that the column
threads through.

**Risk:** Low — additive migration, default value covers existing
rows. Per `MIGRATION_LINTER_OPTIONS` this is a safe class.
**Size:** ~30 LOC source, ~80 LOC tests + migration file.

### PR 7 — Rename `sprite_name` → `backend_handle`

**Schema change:** Two-step migration over two deploys:
1. Add `AgentSession.backend_handle = CharField(...)`. Dual-write
   in app code (write both `sprite_name` and `backend_handle`); read
   from `backend_handle` with fallback to `sprite_name` if empty
   (covers in-flight sessions provisioned during the deploy window).
2. After the dual-write deploy soaks (≥1 day), a follow-up PR drops
   `sprite_name`.

This PR is just step 1. Step 2 is a separate forward-only PR after
soak.

**Code changes:** `models/sessions.py:34`, `views/sessions.py`
(several), `tasks.py::_build_spec_for_session`, plus admin column
display. ~15 references per grep.

**Why:** `sprite_name` is the last backend-specific term in the data
model. After PR 7+8, `git grep -i sprite src/` should show only
`session_service/sprites_backend.py` + `tests/fakes/sprite.py` +
the package name `sprites_backend`.

**Tests:** Migration test, schema-snapshot test (existing
`docs/request_schemas.json` won't change because `sprite_name` was
never in the API), and a dual-write test that exercises the
fallback-read path.

**Risk:** Medium — column rename on a hot table. Two-deploy dance is
the standard mitigation; the migration linter enforces it.
**Size:** ~120 LOC source diff, ~60 LOC tests + migration.

### PR 8 — Generalize `UserSpritesKey` → `UserBackendCredential`

**Schema change:** New table
`UserBackendCredential(user, backend, encrypted_token, created_at)`
unique on `(user, backend)`. Migration backfills every existing
`UserSpritesKey` row as
`UserBackendCredential(user=..., backend="sprites",
encrypted_token=...)`. After backfill soaks, a follow-up PR drops
`UserSpritesKey`.

**Code changes:** `session_service/client.py::get_client(user)`
becomes `get_client(user, backend: str)`; defaults to
`session.backend` when called from a session-scoped path or
`"sprites"` when called from a "first session" path. Admin form
gets a backend dropdown (only "sprites" until ModalBackend lands).

**Why:** Same as PR 7 — the credential model assumes one backend.
This is the cleanest cut that unblocks "user has both Sprites and
Modal tokens".

**Tests:** Migration backfill test (compare row counts and
encrypted-value round-trip), admin form smoke test.

**Risk:** Medium — credential migration is a cryptographic
operation. The encryption key (`FIELD_ENCRYPTION_KEY`) is the same;
we're moving ciphertext, not re-encrypting. But verify the
round-trip in the migration test.
**Size:** ~150 LOC source diff (admin + service + migration), ~120
LOC tests.

## Out of scope (separate plans)

- **`ModalBackend` impl.** A full plan covering Modal sandbox
  primitives, network policy translation, output streaming, and the
  per-runtime install steps under Modal's environment. Land after
  PR 8 so it has a stable Protocol + credential surface to target.
- **Per-user backend selection in the API.** Today `Backend` is
  effectively a server-config thing. Exposing
  `POST /sessions {"backend": "modal"}` is its own API design pass —
  validation, default behavior, error messaging, OpenAPI updates.
- **Caching `BackendClient` across requests.** The 2026-04-18
  research Open Question §1 raised the `httpx.Client` leak. Worth a
  separate perf-focused PR; the Protocol leaves room for it
  (`BackendClient.close()` is part of the contract).
- **Half-wired network policy.** Already noted in
  `thoughts/plans/2026-04-17-network-isolation.md`. Land that on its
  own; this plan inherits whatever shape it takes.

## Notes on cadence

- PRs 1–5 are pure refactors and can land back-to-back over a
  week. None of them require coordination or soak time.
- PRs 6–8 are forward-only schema migrations and need a deploy
  between each (per `docs/runbook.md`). Allow ~1 week per migration
  to soak before the follow-up "drop old column/table" PR.
- After each merge: pull main, rebase the next branch, re-run
  `make mutation-test` + `make test-e2e-fast`. The mutation gate
  may shift if `tasks.py` lines move — update the survivor allowlist
  in `scripts/check_mutmut.py` if needed.
- PRs 1–5 do not require user re-review of the 2026-04-18 research;
  they implement it. PRs 6–8 are schema decisions and warrant a
  pause-and-review at the start of PR 6.

## What carries over from the 2026-04-18 research

- Hard constraints C1–C5 (multi-turn reuse, persistent FS,
  fairy-side output capture, post-creation network policy, no
  secrets in exec env) — every PR here honors them.
- The 7-method Protocol design (refined to 6 here, with
  `set_executable` collapsed into `WorkspaceFS.chmod` per research
  §6).
- The error taxonomy (`BackendError` / `SessionNotFoundError` /
  `ExecutionError`) is implemented as-spec in PR 1.
- The `WorkspaceFS` sub-protocol shape (`write_text`, `chmod`).
- The recommendation that `Command.run() -> int` returns the exit
  code directly instead of raising `ExecError` for non-zero exits.
