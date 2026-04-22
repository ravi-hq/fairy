---
date: 2026-04-21T20:00:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 919a4d4fd679effee2099161911a3146aedc6a64
branch: plans/provision-stage-events
repository: ravi-hq/agent-on-demand
topic: "Add opencode runtime support to agent-on-demand"
tags: [research, team-research, runtimes, opencode, provisioning]
status: complete
method: agent-team
team_size: 5
tracks: [opencode-cli, runtime-execution, provisioning, api-surface, history]
last_updated: 2026-04-21
last_updated_by: Claude Code
---

# Research: Add opencode runtime support to agent-on-demand

**Date**: 2026-04-21
**Researcher**: Claude Code (team-research)
**Git Commit**: [`919a4d4`](https://github.com/ravi-hq/agent-on-demand/commit/919a4d4fd679effee2099161911a3146aedc6a64)
**Branch**: `plans/provision-stage-events`
**Repository**: ravi-hq/agent-on-demand
**Method**: Agent team (5 specialist researchers)

## Research Question

What does it take to add opencode (https://opencode.ai, sst/opencode) as a
supported runtime alongside the existing `claude`, `claude-oauth`, `codex`, and
`gemini` runtimes?

## Summary

Adding opencode is mostly mechanical once three non-trivial issues are addressed:

1. **Sprite base image dependency** — opencode must be pre-installed on the
   Sprite. fairy does not manage the base image; this is a Sprites platform
   change (outside this repo), identical to how `claude`/`codex`/`gemini`
   binaries are provided today.
2. **Env var model mismatch** — opencode reads the native provider env vars
   (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`) directly, but
   fairy's `RuntimeConfig.env_var` is a single constant per runtime. Since
   opencode is a multi-provider *meta-runtime*, its env var is effectively
   model-dependent. This forces a design choice (see Design Decisions below).
   A related test, `test_each_runtime_has_unique_env_var`, will need to adapt
   if opencode reuses `ANTHROPIC_API_KEY`.
3. **Model-flag plumbing** — opencode picks the model via `--model
   provider/model-id` at invocation time. fairy's current `build_turn_command`
   only plumbs `$PROMPT` and `$AOD_SESSION_ID` into the shell environment. A
   new `$AOD_MODEL` (or equivalent) must be written into `/tmp/aod-env` so the
   runtime cmd string can reference it.

Beyond those, the wiring is drop-in: one `RuntimeConfig` entry, one
`_write_mcp_opencode` function, one `_SKILLS_ROOTS` entry, choices migrations
for `Agent.model`/`Agent.runtime`/`UserRuntimeKey.RUNTIME_CHOICES`, new enum
entries and `MODEL_RUNTIME_MAP` rows, and parametrized MCP/skills tests
following the existing patterns. `stream.py` needs no changes — it is already
raw passthrough with no per-runtime parser.

## Research Tracks

### Track 1: Opencode CLI external capabilities
**Researcher**: opencode-researcher
**Scope**: Web research against opencode.ai/docs and sst/opencode

#### Findings

1. **Headless invocation** — `opencode run "<prompt>"` is the one-shot,
   TUI-less, auto-approve command. No `--print` flag; `run` itself is the
   non-interactive mode. ([opencode.ai/docs/cli/](https://opencode.ai/docs/cli/))

2. **Output format** — `--format json` emits raw streaming JSON events to
   stdout; default is human-readable terminal output. The JSON event schema is
   **not publicly documented** — only that "raw JSON events" are emitted.
   ([opencode.ai/docs/cli/](https://opencode.ai/docs/cli/))

3. **Session resume** — Two flags: `--continue`/`-c` resumes the *last*
   session; `--session <id>`/`-s <id>` resumes by ID. Session IDs are
   **assigned by opencode on first run** — there is no pre-specification flag
   analogous to Claude's `--session-id`. To use `--session`, the caller must
   parse and persist the ID from turn-1 stdout.
   ([opencode.ai/docs/cli/](https://opencode.ai/docs/cli/))

4. **Auth env vars** — Opencode reads native provider env vars directly (no
   `OPENCODE_API_KEY`):
   - `ANTHROPIC_API_KEY` (Anthropic)
   - `OPENAI_API_KEY` (OpenAI)
   - `GOOGLE_GENERATIVE_AI_API_KEY` or `GEMINI_API_KEY` (Google)
   - `AWS_*` (Bedrock), `AZURE_RESOURCE_NAME` (Azure), etc.
   - `OPENCODE_CONFIG_CONTENT` allows inline JSON config injection (useful for
     ephemeral containers).
   ([opencode.ai/docs/providers/](https://opencode.ai/docs/providers/))

5. **Model selection** — `--model provider/model-id` or `-m` flag; model list
   is dynamic (fetched from models.dev at runtime, 75+ providers). Canonical
   format examples:
   - `anthropic/claude-opus-4-7`, `anthropic/claude-sonnet-4-6`, `anthropic/claude-haiku-4-5`
   - `openai/gpt-4o`, `openai/gpt-5-mini`, `openai/o1-mini`
   - `google/gemini-2.5-pro-preview-05-06`, `google/gemini-2.5-flash-lite-preview-09-2025`
   ([opencode.ai/docs/cli/](https://opencode.ai/docs/cli/))

6. **MCP support** — Config lives under an `"mcp"` key in
   `~/.config/opencode/opencode.json`. Format differs from Claude's in two
   small ways: `type` values are `"local"`/`"remote"` (not `"stdio"`/`"http"`),
   `command` is a **single array combining command+args** (not
   `command`+`args` split), and env is keyed as `"environment"` (not `"env"`).
   ([opencode.ai/docs/mcp-servers/](https://opencode.ai/docs/mcp-servers/))

7. **Skills convention** — Opencode natively reads agentskills.io-style
   `SKILL.md` files with YAML frontmatter. Paths searched (in order):
   `.opencode/skills/<name>/SKILL.md` (project),
   `~/.config/opencode/skills/<name>/SKILL.md` (global — i.e.
   `/home/sprite/.config/opencode/skills/<name>/SKILL.md`),
   `.claude/skills/<name>/SKILL.md` (also read — shared with Claude Code),
   `.agents/skills/<name>/SKILL.md`. Note: the global path is
   `~/.config/opencode/skills/`, NOT `~/.opencode/skills/` (opencode-researcher
   corrected an earlier assumption here).
   ([opencode.ai/docs/skills/](https://opencode.ai/docs/skills/))

8. **Install on Debian** — Two options: `npm i -g opencode-ai@latest` (package
   name is `opencode-ai`, not `opencode`), or `curl -fsSL
   https://opencode.ai/install | bash` (installs to `~/.opencode/bin` or
   `$OPENCODE_INSTALL_DIR`). No apt/deb package.
   ([sst/opencode README](https://github.com/sst/opencode))

### Track 2: Runtime execution wiring
**Researcher**: runtime-execution-researcher
**Scope**: `src/agent_on_demand/runtimes.py`, `session_service/{turn,tasks,client,specs}.py`, `stream.py`, `models/sessions.py`

#### Findings

1. **RuntimeConfig contract** — Four fields: `name`, `cmd`, `continue_cmd`,
   `env_var` ([`runtimes.py:48-53`](src/agent_on_demand/runtimes.py)).
   Substitution is **pure shell expansion**, not Python formatting. The turn
   command is assembled in
   [`tasks.py::build_turn_command` (lines 98-109)](src/agent_on_demand/session_service/tasks.py)
   as `set -a; source /tmp/aod-env; set +a; PROMPT=$(cat); export PROMPT; exec
   <runtime_cmd>`. `$PROMPT` and `$AOD_SESSION_ID` resolve inside the Sprite's
   `bash -c` invocation ([`tasks.py:371-381`](src/agent_on_demand/session_service/tasks.py)).

2. **Per-runtime resume patterns** diverge:
   - `claude`: pre-generated UUID as `--session-id` turn 1, `--resume` turn N (stored as `runtime_session_id` on `AgentSession`).
   - `codex`: `--last` on resume (no ID plumbing).
   - `gemini`: bare `--resume` flag (no ID plumbing).
   - `claude-oauth`: identical to `claude` but with `CLAUDE_CODE_OAUTH_TOKEN`.

3. **No wrapper script** — The `/run-agent.sh` dispatcher has been fully
   removed; see `c677159` "Rip out the /run-agent.sh dispatcher". Everything
   is inline in `build_turn_command` now.

4. **Raw passthrough for stream output** — [`stream.py:39-53`](src/agent_on_demand/stream.py)
   emits `AgentSessionLog.data` unchanged. `data` is a raw UTF-8 decoded
   stdout/stderr `TextField` ([`models/sessions.py:112-153`](src/agent_on_demand/models/sessions.py)).
   **No per-runtime parser exists.** Adding a new runtime requires zero
   changes to `stream.py` or any log-ingestion code.

5. **Mode selection is view-driven** — `POST /sessions` uses `mode="run"`
   ([`views/sessions.py:264`](src/agent_on_demand/views/sessions.py)); `POST
   /sessions/{id}/prompt` uses `mode="continue"`
   ([`views/sessions.py:425`](src/agent_on_demand/views/sessions.py)).
   `build_turn_command(runtime, mode)` picks `cmd` vs `continue_cmd`
   accordingly.

6. **API key plumbing** — `UserRuntimeKey.get_key_for(user, runtime)` →
   `SessionSpec.api_key` → `_write_env_file` writes `{env_var}={api_key}` to
   `/tmp/aod-env` on the Sprite
   ([`provisioning.py:126`](src/agent_on_demand/session_service/provisioning.py)).
   Keys never live in the web process during transit — the worker decrypts,
   writes to Sprite filesystem, discards.

7. **Draft opencode RuntimeConfig** — see Design Decisions section below; two
   variants (`--continue` vs `--session <id>`) depending on how we handle
   session-id capture.

### Track 3: Per-runtime provisioning
**Researcher**: provisioning-researcher
**Scope**: `src/agent_on_demand/session_service/{provisioning,specs,client}.py`, `tests/test_tools_mcp.py`, `tests/test_skills.py`

#### Findings

1. **Provisioning stages** — Only two of eight stages branch on runtime name:
   `mcp_config` (via `_write_mcp_config` at
   [`provisioning.py:227-238`](src/agent_on_demand/session_service/provisioning.py))
   and `skills` (via `_SKILLS_ROOTS` lookup at
   [`provisioning.py:305-318`](src/agent_on_demand/session_service/provisioning.py)).
   Everything else (network policy, env file, packages, repo clone, user
   setup) is runtime-agnostic.

2. **MCP config paths and formats per runtime**:
   - Claude: `/home/sprite/.claude.json`, JSON `{"mcpServers": {...}}`, `type: "stdio"|"http"`, `command`+`args` split, `env` key.
   - Codex: `/home/sprite/.codex/config.toml`, TOML `[mcp_servers.NAME]` sections, `bearer_token_env_var` extracted from Authorization header.
   - Gemini: `/home/sprite/.gemini/settings.json`, JSON with `httpUrl` (not `url`) for URL servers, `trust: true` everywhere.
   - Opencode (new): `/home/sprite/.config/opencode/opencode.json`, JSON `{"mcp": {...}}`, `type: "local"|"remote"`, `command` as single array, `environment` (not `env`).

3. **Skills roots** ([`provisioning.py:30-35`](src/agent_on_demand/session_service/provisioning.py)):
   ```python
   _SKILLS_ROOTS = {
       "claude":       "/home/sprite/.claude/skills",
       "claude-oauth": "/home/sprite/.claude/skills",
       "codex":        "/home/sprite/.codex/skills",
       "gemini":       "/home/sprite/.gemini/skills",
   }
   ```
   Opencode's global skills path is `/home/sprite/.config/opencode/skills/`
   (not `/home/sprite/.opencode/skills/` as initially drafted). An alternative
   is to reuse `"/home/sprite/.claude/skills"` since opencode also reads that
   path — zero new directory, but mixes runtimes in one tree. Recommend the
   dedicated path for clarity.

4. **Env file is runtime-agnostic** — `_write_env_file`
   ([`provisioning.py:122-139`](src/agent_on_demand/session_service/provisioning.py))
   takes `spec.runtime.env_var` and writes `{env_var}=<api_key>`. No
   per-runtime branching. For opencode this breaks down because opencode
   wants the native provider env var, not a constant.

5. **Runtime binaries are pre-installed by the Sprites platform** — There is
   no Dockerfile, no `render.yaml` runtime-install step, no Sprite base image
   definition in this repo. The `claude`/`codex`/`gemini` binaries are
   expected to already be on PATH. **Adding opencode requires a Sprites
   platform base image change — outside this codebase.** This is a hard
   deployment dependency.

6. **Test patterns** — Parametrized fake-sprite tests assert on
   `fake_sprites.last_sprite().write_map()`:
   - MCP: [`tests/test_tools_mcp.py`](tests/test_tools_mcp.py) —
     `test_claude_mcp_url_server_writes_json` (line 55),
     `test_codex_mcp_writes_toml` (line 138),
     `test_gemini_mcp_writes_settings_json` (line 197).
   - Skills: [`tests/test_skills.py`](tests/test_skills.py) —
     `test_claude_skill_writes_skill_md_under_dot_claude` (line 72),
     `test_codex_skill_writes_under_dot_codex` (line 95),
     `test_gemini_skill_writes_under_dot_gemini` (line 120).
   A new `test_opencode_mcp_writes_opencode_json` and
   `test_opencode_skill_writes_under_dot_opencode` must follow the same
   pattern.

### Track 4: API surface, model mapping, and tests
**Researcher**: api-surface-researcher
**Scope**: `src/agent_on_demand/{runtimes,views/,models/,urls}.py`, `tests/test_runtimes.py`, `tests/e2e/`

#### Findings

1. **Model validation is Pydantic, at agent-create time** —
   [`views/agents.py:116-121`](src/agent_on_demand/views/agents.py) rejects
   unknown model strings with 422. `model` is validated against
   `AgentModel.values()`.

2. **No automatic model→runtime derivation** — `MODEL_RUNTIME_MAP` at
   [`runtimes.py:32-45`](src/agent_on_demand/runtimes.py) **is never imported
   or called anywhere** outside its declaration. `runtime` is an **independent
   client-supplied field** on agent create, validated against `RUNTIMES` keys
   ([`agents.py:238-242`](src/agent_on_demand/views/agents.py)). The Agent row
   stores both; session-create reads `agent_obj.runtime` directly
   ([`sessions.py:203`](src/agent_on_demand/views/sessions.py)). A client *can*
   submit mismatched `model` + `runtime` today — there is no cross-validation.
   The map is effectively documentation.

3. **Runtime is a client-facing field** — Required on `POST /agents`, returned
   in all serializations. Adding `"opencode"` as a valid value is not
   API-breaking; clients just gain a new allowed string.

4. **Migration needed** — Both `Agent.model` and `Agent.runtime` use Django
   `choices=`, which means Django emits `AlterField` migrations when choices
   change ([`models/agents.py:18-19`](src/agent_on_demand/models/agents.py)).
   Also [`models/auth.py:51`](src/agent_on_demand/models/auth.py) defines
   `UserRuntimeKey.RUNTIME_CHOICES`, which also changes. No DB-level CHECK
   constraints; Postgres stores the values as opaque strings.
   `AgentVersion.model`/`AgentVersion.runtime` are plain `CharField`s with no
   choices — no migration needed there.

5. **Hidden coupling — hardcoded runtime set** —
   [`tests/test_runtimes.py:5-6`](tests/test_runtimes.py):
   ```python
   assert set(RUNTIMES) == {"claude", "codex", "gemini", "claude-oauth"}
   ```
   This will fail when opencode is added and must be updated. It is the *only*
   Literal-style guard in the codebase — no view or serializer hardcodes the
   runtime list.

6. **Unique-env_var test collision risk** —
   [`tests/test_runtimes.py:63-65`](tests/test_runtimes.py)
   `test_each_runtime_has_unique_env_var` asserts all `env_var` values are
   distinct. If opencode uses `ANTHROPIC_API_KEY` (same as `claude`), this
   test breaks.

7. **E2E parametrization** —
   [`tests/e2e/conftest.py:21-26`](tests/e2e/conftest.py) has `RUNTIME_MODELS`
   driving parametrized session lifecycle tests in
   [`tests/e2e/test_sessions.py:24-29`](tests/e2e/test_sessions.py). Adding
   `"opencode": "<model>"` and setting `E2E_RUNTIMES=opencode` is all that's
   needed — no new test files.

8. **Docs updates** — `README.md:69-75` has a manual runtime/model table that
   must include opencode. No OpenAPI update needed beyond schema auto-gen.

### Track 5: Historical context
**Researcher**: history-researcher
**Scope**: `thoughts/research/`, `thoughts/plans/`, git log of `runtimes.py` and `provisioning.py`, `README.md`, `CLAUDE.md`

#### Findings

1. **Prior runtime additions** — All three original runtimes (claude, codex,
   gemini) landed in one commit at project inception (`3927cf9`). The only
   post-launch runtime addition is `claude-oauth` (commit `cfe0170`), a 1-line
   env-var change. There is no "add a runtime" precedent of the size opencode
   will be.

2. **MCP rollout conventions** — Documented in
   `thoughts/research/2026-04-16-agent-tools-and-mcp.md` and
   `thoughts/plans/2026-04-17-tools-mcp-examples-docs.md`. Core rules: MCP
   auth lives in `Environment.env_vars` (encrypted), not on the agent; max 20
   servers per agent; each runtime has a separate writer function and
   distinct config path.

3. **Skills rollout conventions** — `thoughts/research/2026-04-17-agent-skills-support.md`
   and `thoughts/plans/2026-04-17-skills-phase-1.md`. All three runtimes
   implement agentskills.io SKILL.md format — opencode natively supports the
   same, which is a free compatibility win. Phase 1 is inline content only;
   URL/git-fetch is Phase 3 (gated on public skill vulnerability rate).

4. **Session service refactor has landed** — Commits `6a43cf0`, `c677159`,
   `ae36ca6`, `c03f359`. `provision_session(user, spec: SessionSpec)` is the
   current shape; no wrapper script. Planning for opencode should assume the
   current (post-refactor) architecture.

5. **CLAUDE.md convention** — Repeats the rule: "new models must be added to
   both `AgentModel` *and* `MODEL_RUNTIME_MAP`". No `examples/` directory
   exists; no docs/ update required by convention.

6. **No prior opencode references** — A full-repo grep for "opencode"
   returned zero hits across `.md`, `.py`, `.toml`, `.yaml`. Fresh territory.

## Cross-Track Discoveries

These emerged from connecting findings across tracks:

- **Env var model mismatch is the biggest design issue.** `RuntimeConfig.env_var`
  is a single constant per runtime (Track 2 finding 6), but opencode reads
  native provider env vars that vary by model (Track 1 finding 4). Every
  existing runtime has a 1:N model-to-env-var relationship (all claude models
  → `ANTHROPIC_API_KEY`); opencode has a 1:1 model-to-env-var relationship
  per registered model. This needs a design choice (below), not just a config
  entry.
- **Model flag requires new env plumbing.** Track 1 confirms opencode picks
  model via `--model provider/model-id`, but Track 2 shows the turn command
  only plumbs `$PROMPT` and `$AOD_SESSION_ID` — not `$MODEL`. The env file
  writer (`_write_env_file`) must grow to include the model string so the cmd
  can reference `"$AOD_MODEL"`. This is net-new infrastructure, but small.
- **Skills path has a free compatibility win.** Track 1 finding 7 confirms
  opencode reads `.claude/skills/` as a fallback. Combined with Track 3's
  `_SKILLS_ROOTS` design, we could either give opencode its own path or point
  it at the existing claude tree. Recommend its own path for clarity.
- **Base image dependency sits outside this repo.** Tracks 3 and 4 both
  independently flagged that runtime binaries are provided by the Sprites
  platform, not fairy. The opencode code changes can merge and ship, but
  sessions will fail until the base image is updated. This should be a
  coordination step with whoever owns the Sprite base image.

## Design Decisions

These require explicit choices before implementation starts. Flagging for
CEO/eng review.

### D1. Session resume: `--continue` (simple) vs `--session <id>` (explicit)

**`--continue` (recommended)**: Matches the codex `--last` pattern. No new
session-id capture infrastructure. Works because fairy already enforces
one-Sprite-per-session. Cost: if opencode ever spawns a subsession or the
Sprite restarts with state, "last" is ambiguous — but neither condition holds
today.

**`--session <id>`**: Matches the claude pre-generated UUID pattern. Requires
parsing session ID from turn-1 stdout and persisting to
`AgentSession.runtime_session_id`. New parser, new contract with opencode's
undocumented event schema, more failure modes.

Recommend `--continue` unless someone has a specific reason to need explicit
session IDs.

### D2. Env var: per-provider vs one shared `ANTHROPIC_API_KEY`

Opencode reuses `ANTHROPIC_API_KEY` for its Anthropic provider, colliding with
claude's. Three options:

- **D2a**: Set opencode's `env_var` to a provider-native name directly (e.g.
  `"ANTHROPIC_API_KEY"`). Pro: zero translation. Con: breaks
  `test_each_runtime_has_unique_env_var`; users with both claude and opencode
  registered must use the same API key value for both.
- **D2b**: AOD-managed alias (e.g. `OPENCODE_API_KEY`). Extend
  `_write_env_file` to also export the provider-native name based on
  model/provider. Pro: preserves the uniqueness test, independent user keys.
  Con: small extension to `_write_env_file` logic; branches on model provider.
- **D2c**: `OPENCODE_CONFIG_CONTENT` with inline config. Avoids env var plumbing
  entirely. Con: more complex for little benefit; still need a per-provider
  key somewhere.

Recommend **D2b** — preserves per-runtime uniqueness semantics and lets a user
rotate opencode keys independent of their direct claude keys.

### D3. Model registration: single runtime vs one-per-provider

Opencode is a multi-provider meta-runtime. Options:

- **D3a (recommended)**: Single `"opencode"` runtime; `AgentModel` gains entries
  like `OPENCODE_CLAUDE_HAIKU_4_5 = "anthropic/claude-haiku-4-5"`. The
  `provider/` prefix is part of the stored string and gets passed to `--model`
  via `$AOD_MODEL`.
- **D3b**: One runtime per provider (`opencode-anthropic`, `opencode-openai`,
  `opencode-google`). Cleaner D2 story (each runtime has one env_var), but
  triples the maintenance surface and bloats `_SKILLS_ROOTS` / `RUNTIMES` /
  migration rows.

Recommend **D3a** — matches the existing pattern where one runtime covers many
models.

## Code References

| File | Tracks | What changes |
|------|--------|--------------|
| `src/agent_on_demand/runtimes.py:5-20` | 2, 4 | Add `OPENCODE_*` entries to `AgentModel` |
| `src/agent_on_demand/runtimes.py:32-45` | 4 | Add `"<model>": "opencode"` rows to `MODEL_RUNTIME_MAP` |
| `src/agent_on_demand/runtimes.py:56-87` | 2 | Add `"opencode": RuntimeConfig(...)` to `RUNTIMES` |
| `src/agent_on_demand/session_service/provisioning.py:30-35` | 3 | Add `"opencode": "/home/sprite/.config/opencode/skills"` to `_SKILLS_ROOTS` |
| `src/agent_on_demand/session_service/provisioning.py:122-139` | 2, 3 | Extend `_write_env_file` to write `AOD_MODEL` and opencode provider env var (D2b) |
| `src/agent_on_demand/session_service/provisioning.py:227-238` | 3 | Add `elif runtime_name == "opencode": _write_mcp_opencode(sprite, servers)` |
| `src/agent_on_demand/session_service/provisioning.py` (new) | 3 | Add `_write_mcp_opencode(sprite, servers)` helper |
| `src/agent_on_demand/session_service/tasks.py:98-109` | 2 | Confirm `$AOD_MODEL` expands in the sourced env |
| `src/agent_on_demand/models/agents.py:18-19` | 4 | Choices on `model`/`runtime` regenerate → new migration |
| `src/agent_on_demand/models/auth.py:51` | 4 | `RUNTIME_CHOICES` on `UserRuntimeKey` → migration |
| `tests/test_runtimes.py:5-6` | 4 | Update hardcoded `{"claude","codex","gemini","claude-oauth"}` set |
| `tests/test_runtimes.py:63-65` | 4 | Adjust `test_each_runtime_has_unique_env_var` per D2 choice |
| `tests/test_tools_mcp.py` | 3 | Add `test_opencode_mcp_*` parametrization |
| `tests/test_skills.py` | 3 | Add `test_opencode_skill_writes_under_dot_opencode` |
| `tests/e2e/conftest.py:21-26` | 4 | Add `"opencode": "<canonical-model>"` to `RUNTIME_MODELS` |
| `README.md:69-75` | 4, 5 | Add opencode row to runtime/model table |
| **Sprite base image** (external) | 3 | Pre-install `opencode` binary (`npm i -g opencode-ai@latest` or curl script) |

## Drafted Artifacts

Assuming D1=`--continue`, D2=`OPENCODE_API_KEY` alias (D2b), D3=single runtime
(D3a):

### `runtimes.py` — `RuntimeConfig` entry

```python
"opencode": RuntimeConfig(
    name="opencode",
    cmd='opencode run --model "$AOD_MODEL" --format json "$PROMPT"',
    continue_cmd='opencode run --model "$AOD_MODEL" --format json --continue "$PROMPT"',
    env_var="OPENCODE_API_KEY",
),
```

### `provisioning.py` — `_write_mcp_opencode`

```python
def _write_mcp_opencode(sprite: Sprite, servers: list[McpServerSpec]) -> None:
    sprite.command("mkdir", "-p", "/home/sprite/.config/opencode").run()
    config: dict[str, dict] = {}
    for s in servers:
        if s.type == "url":
            entry: dict = {"type": "remote", "url": s.url, "enabled": True}
            if s.headers:
                entry["headers"] = s.headers
        elif s.type == "stdio":
            entry = {
                "type": "local",
                "command": [s.command, *s.args],
                "enabled": True,
            }
            if s.env:
                entry["environment"] = s.env
        config[s.name] = entry
    fs = sprite.filesystem()
    (fs / "home/sprite/.config/opencode/opencode.json").write_text(
        json.dumps({"mcp": config}, indent=2)
    )
```

### `_write_env_file` extension (D2b sketch)

Add `AOD_MODEL` for all runtimes (harmless constant; only opencode reads it),
plus translate `OPENCODE_API_KEY` into the native provider env var based on
the model's provider prefix:

```python
# Inside _write_env_file, after existing lines:
if spec.agent_model:  # e.g. "anthropic/claude-haiku-4-5"
    lines.append(f"AOD_MODEL={shlex.quote(spec.agent_model)}")
    if spec.runtime.name == "opencode":
        provider = spec.agent_model.split("/", 1)[0]
        native = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google": "GEMINI_API_KEY",
        }.get(provider)
        if native:
            lines.append(f"{native}={shlex.quote(spec.api_key)}")
```

## Historical Context

- `thoughts/research/2026-04-16-agent-tools-and-mcp.md` — original MCP design,
  cross-runtime config-path table, validation rules.
- `thoughts/research/2026-04-17-agent-skills-support.md` — skills architecture,
  agentskills.io conformance, SKILL_EOF injection hazard.
- `thoughts/plans/2026-04-19-session-service-api-refactor.md` — the
  `SessionSpec` / wrapper-removal refactor that shaped the current
  `provision_session` signature.
- Commits to reference for style/scope: `3927cf9` (original runtimes batch),
  `cfe0170` (claude-oauth 1-line addition — tiny counter-example to opencode's
  complexity).

## Open Questions

1. **Who owns the Sprite base image?** Adding opencode to the base image is a
   prerequisite. This doc doesn't answer that — it's a coordination question
   for whoever controls the Sprites platform image.
2. **Which opencode models do we register first?** Suggest starting with one
   cheap model per provider (`anthropic/claude-haiku-4-5`, `openai/gpt-4o`,
   `google/gemini-2.5-flash-lite-preview-09-2025`) to validate the integration
   end-to-end without model-list bloat.
3. **Does UserRuntimeKey need a per-provider key story?** D2b stores one
   `OPENCODE_API_KEY` per user. If a user wants to mix opencode+Anthropic and
   opencode+OpenAI in different agents, they'd need two separate
   UserRuntimeKey rows — but today UserRuntimeKey is keyed on `(user,
   runtime)`. This would need a small schema extension (or a convention that
   OPENCODE_API_KEY covers all providers a user actually uses).
4. **JSON event schema stability.** Opencode's `--format json` output is
   undocumented. If we ever add a per-runtime parser, we'd need to reverse-
   engineer or upstream a PR to document the schema. Not blocking for initial
   support (stream.py is raw passthrough).
