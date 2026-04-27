# Session-service quality pass

A sequenced PR plan for the next phase of session_service hardening.

## Context

The arc that started in #173 (mutation testing for danger-zone modules) and
ran through #210 (`build_provision_script`) extracted every pure helper from
the *provisioning* path and put it under mutmut. The shape memory predicted
in `project_session_service_refactor.md` — `provision_session` taking a
`SessionSpec`, `/run-agent.sh` collapsing to a dispatcher, the `aod-env`
file replacing in-script secret injection — is already in place.

What's left in `session_service/` is the *execution* path:

| Module           | Lines | What lives there                                        |
|------------------|-------|---------------------------------------------------------|
| `tasks.py`       |   612 | Three Procrastinate tasks; the big one is `execute_turn`|
| `provisioning.py`|   355 | `provision_session` orchestrator + per-stage I/O helpers|
| `turn.py`        |    37 | Trivial enqueue wrapper                                 |

Both `tasks.py` and `provisioning.py` mix pure logic (argv assembly, env-file
composition, network-policy construction, status computation, spec
rehydration) with imperative I/O (Sprite calls, ORM writes, threading,
posthog capture). The pure parts are not mutation-tested today and several
are not directly unit-tested either.

## Strategy

Same playbook that worked through #197–#210:

1. Find pure logic embedded in an imperative function.
2. Extract it to a new module, callable with primitive args (no Django/Sprite
   coupling).
3. Add sync-only tests that work under `hammett` (no pytest plugins).
4. Add to `[tool.mutmut].paths_to_mutate` + `tests_dir`.
5. Verify 100% kill rate (or document equivalent mutants in
   `scripts/check_mutmut.py`).

Each PR is independently mergeable; later PRs can build on earlier ones,
but reordering is fine. Aim for ≤300 net LOC per PR so review stays cheap.

## PR sequence

### PR 1 — Extract `build_turn_argv` to its own module + mutation tests

**File moves:** `tasks.py:104-120` → new `session_service/turn_argv.py`.
**Surface:** `build_turn_argv(runtime, spec, mode) -> list[str]` and the
`_ENV_SOURCE_SHIM` constant.

**Why:** This is the smallest pure unit in the file (16 lines) and the
exact place a quoting bug would silently break env-var sourcing for every
turn. Currently zero direct tests — coverage exists only transitively via
e2e and integration tests against running runtimes.

**Mutation-killing tests:**
- Argv starts with `["bash", "-lc", _ENV_SOURCE_SHIM, "--", ...]`.
- Shim does `set -a; source /tmp/aod-env; set +a; exec "$@"` exactly.
- The runtime's `build_command(spec, mode)` output is appended verbatim.
- Mode is forwarded as `"run"` and `"continue"` literally.

**Risk:** None — pure refactor.
**Size:** ~120 LOC test file, 30 LOC source module.

### PR 2 — Extract env-file body composition + mutation tests

**Refactor:** `provisioning.py::_write_env_file` currently does both
"fetch user credentials from ORM" and "compose the KEY=value body".
Split into:
- `build_env_file_body(spec, credentials) -> str` — pure, takes a
  `list[tuple[env_name, value]]` for credentials.
- `_write_env_file(sprite, spec, session_id)` — keeps the ORM fetch +
  Sprite filesystem write, calls `build_env_file_body`.

**Why:** The precedence rules (credentials → session metadata →
`environment.env_vars`) and `shlex.quote` calls are exactly the kind of
silent-corruption bugs that mutmut catches. Currently these are tested
only at the integration layer where a stale `KEY` from a different
ordering would still produce a "valid" env file.

**Mutation-killing tests:**
- Empty inputs → exactly `"\n"`.
- Credentials precede session metadata, which precedes `env_vars`.
- `shlex.quote` is applied to every value (defense-in-depth — payloads
  with quotes / dollar signs land single-quoted).
- A `runtime_session_id=None` skips the `AOD_SESSION_ID=` line; same for
  empty `model`.
- An `env_vars` entry is sorted by key (already pinned in source — pin
  in tests too).

**Risk:** Light — `_write_env_file` keeps its signature; only its body
changes. `tasks.py` and views don't touch it.
**Size:** ~150 LOC test file, ~40 LOC source module.

### PR 3 — Extract network-policy construction + mutation tests

**Refactor:** Pull the `PolicyRule(...)` list-building out of
`provisioning.py::_apply_network_policy` into a pure
`build_network_policy(env) -> NetworkPolicy | None` helper.

**Why:** The `deny *` rule must be appended *last* so allow-listed hosts
take precedence — a reordering would silently let everything through. The
"None when networking_type != 'limited'" branch is the only thing
preventing accidental policy attachment for unrestricted environments.

**Mutation-killing tests:**
- `networking_type="limited"` with two allowed hosts → three rules
  ending with `domain="*", action="deny"`.
- Empty allowed hosts → only the deny-all rule.
- `networking_type="open"` (or any non-limited) → returns `None`.
- `env=None` → returns `None`.

**Risk:** None — pure refactor.
**Size:** ~120 LOC test file, ~25 LOC source module.

### PR 4 — Extract `compute_final_status` from `_execute_turn_body`

**Refactor:** `tasks.py:533-543` has a 10-line conditional that turns the
`result_holder` (one of `("exit", N)` or `("error", str)` or empty) into
`(final_status, exit_code)`. Pull to `session_service/turn_outcome.py`.

**Why:** Three input shapes × two output fields = exactly the kind of
branching where `==` → `!=` or `0` → `1` mutations slip through. Today
it's tested only via the full `execute_turn` integration paths, which
are slow and don't exercise every branch.

**Mutation-killing tests:**
- `("exit", 0)` → `("completed", 0)`.
- `("exit", 1)` → `("failed", 1)`.
- `("exit", -15)` (signal-style) → `("failed", -15)`.
- `("error", "boom")` → `("failed", None)`.
- Empty list (thread crashed before recording) → `("failed", None)`.

**Risk:** None — pure refactor.
**Size:** ~80 LOC test file, ~25 LOC source module.

### PR 5 — Move `_build_spec_for_session` to its own module

**Refactor:** `tasks.py:211-254` rehydrates a `SessionSpec` from
persisted `AgentSession` state. Move to
`session_service/spec_factory.py`.

**Why:** This is the *only* code path that translates ORM rows into the
SessionSpec shape that `provision_session` and `execute_turn` consume.
A bug here (missing field, wrong default, type mismatch) silently breaks
every session. It's not mutmut-able because it touches Django ORM, but
it deserves direct pytest-django unit tests instead of being exercised
only through e2e.

**Tests:**
- `pytest.mark.django_db` tests with factory-built `AgentSession`,
  `Agent`, `Environment`, `SessionResource`, `UserCredential` rows.
- Cover each branch in the rehydration: agent absent (model/skills/mcp
  defaults), inline skills, github skills (with and without name), repos
  with and without tokens, missing `runtime_session_id`.

**Risk:** Light — pure code move with imports to update. `tasks.py`
shrinks; `spec_factory` is new.
**Size:** ~250 LOC test file (it has many branches), ~70 LOC source.

### PR 6 — Split `provisioning.py` per-stage helpers into a stages module

**Refactor:** `provisioning.py` still owns `_install_runtime`,
`_apply_network_policy`, `_write_env_file`, `_write_git_credentials`,
`_run_provision_setup`, `_write_runtime_config`, `_write_skills`. Move
to `session_service/provisioning_stages.py`. `provisioning.py` keeps
`provision_session`, `resume_session`, `destroy_session`,
`emit_stage_event`, `stage_timer`, and the stage-name constants.

**Why:** Today `provisioning.py` is 355 lines of mixed orchestrator +
stage I/O. After the split it's ~150 lines of pure orchestration. Each
stage helper is independently testable with a stub Sprite (no mutmut —
they all do I/O).

**Tests:** Add per-stage unit tests using a fake Sprite (a small class
that records calls). Many of these are already partially covered by
existing tests; the split makes them easier to reach with direct
imports.

**Risk:** Light — code move + import rewrites. CI's existing
`test_session_service.py` (or equivalent) should still pass unchanged.
**Size:** ~200 LOC test file additions, mostly moves.

### PR 7 — Drop vestigial `sprite` arg from `run_turn`

**Refactor:** `turn.py::run_turn` takes a `sprite` param it never uses
(the worker re-opens the handle from `session.sprite_name`). Drop it
and update callers in `views/`.

**Why:** A dead param is a maintenance trap — readers waste time
chasing it, and a future refactor that "uses" it would silently break
the worker re-entry contract. Already noted in the docstring as
"drop in a follow-up".

**Tests:** No new tests needed; the existing view tests must pass.

**Risk:** Touches view code (small surface — grep for `run_turn(`).
Mechanical.
**Size:** ~10 LOC source change, ~30 LOC test diff if any.

## Out of scope (separate plans)

These are *known* but deliberately not included so this plan stays
shippable in 7 small PRs:

- **`_execute_turn_body` itself** — the threading + queue draining +
  bulk_create-retry core. Splitting this needs a research pass on how
  to mock the Sprite/SDK threading model, plus a probably-larger refactor
  to a coroutine or sync-loop shape. Tackle after the easy extractions
  shrink the surrounding code.
- **`--dangerously-skip-permissions` asymmetry** (Muddy Zone 7 in the
  prior session-service research note).
- **`cmd_thread.join(timeout=5.0)` zombie path** (Muddy Zone 8) — already
  has a posthog signal; needs a real fix once we decide on cancellation
  semantics.
- **Auto-revert UX** — separate roadmap item (item 6 in the agent-safety
  roadmap memory).

## Notes on cadence

- Land in order; each PR builds the next's import surface but does not
  *block* it (PRs 1-4 are fully independent).
- The user reviews and merges PRs manually. Do **not** auto-merge.
- After each merge: pull main, rebase the next branch, re-run
  `make mutation-test` to confirm the cumulative kill rate.
