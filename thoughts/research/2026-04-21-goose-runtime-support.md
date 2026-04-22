---
date: 2026-04-21
researcher: Claude Code (team-research skill)
git_commit: 919a4d4fd679effee2099161911a3146aedc6a64
branch: plans/provision-stage-events
repository: ravi-hq/agent-on-demand
topic: "Adding Goose (Block coding agent) as a runtime"
tags: [research, team-research, runtimes, goose, provisioning]
status: complete
method: agent-team
team_size: 4
tracks: [runtime-abstraction, provisioning-surface, goose-cli, api-tests-surface]
last_updated: 2026-04-21
last_updated_by: Claude Code
---

# Research: Adding Goose (Block coding agent) as a runtime

**Date**: 2026-04-21
**Researcher**: Claude Code (team-research)
**Git Commit**: [`919a4d4`](https://github.com/ravi-hq/agent-on-demand/commit/919a4d4fd679effee2099161911a3146aedc6a64)
**Branch**: `plans/provision-stage-events`
**Repository**: ravi-hq/agent-on-demand
**Method**: Agent team (4 specialist researchers)

## Research Question

What does it take to add support for **Goose** (Block's open-source coding agent, https://github.com/block/goose) as a fourth runtime alongside the existing `claude`, `codex`, `gemini` runtimes?

## Summary

Mechanically, adding Goose is small: one entry in `RUNTIMES`, model entries in `AgentModel`/`MODEL_RUNTIME_MAP`, two short additions in `provisioning.py` (MCP writer + skills root), updates to a handful of tests and docs. The Goose CLI itself is friendly to headless use — `goose run --text "..." --output-format stream-json --mode auto --name <id>`, with `--resume` for continuation — and the install is a single curl script.

**The hard part is architectural, not mechanical.** Goose is provider-agnostic: it pairs `GOOSE_PROVIDER` with `GOOSE_MODEL` and reads the provider's native API key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …). Our current contract assumes one runtime → one model namespace → one `env_var` → one `UserRuntimeKey` row. Three concrete strain points fall out of that mismatch:

1. **`RuntimeConfig.env_var` is singular**, but Goose needs at least two variables set at launch (provider key + provider/model selection).
2. **`AgentModel` is a flat enum**, but Goose's identity is `(provider, model)`, so model strings have to encode both — the codex pattern (multiple model strings → one runtime) extends to Goose, just with more entries.
3. **`tests/test_runtimes.py::test_each_runtime_has_unique_env_var`** asserts uniqueness of `env_var` across runtimes; if Goose-with-Anthropic uses `ANTHROPIC_API_KEY`, that assertion fires immediately — and so does the design question it's enforcing (do we want two different runtimes pulling from the same `UserRuntimeKey`?).

Recommended path: restrict Goose to one provider per `Agent` row (encoded in the model string), reuse the provider's existing `UserRuntimeKey` (or namespace it as e.g. `goose-anthropic`), and decide explicitly whether `RuntimeConfig` grows a second-env-var slot or whether provider-fixed env vars (`GOOSE_PROVIDER`, `GOOSE_MODEL`) get inlined into `cmd` / sourced from a written `~/.config/goose/config.yaml`. The decision should be made before code lands.

## Research Tracks

### Track 1: Runtime abstraction contract
**Researcher**: runtime-researcher
**Scope**: `runtimes.py`, `session_service/{specs,tasks,provisioning}.py`, `models/{agents,sessions,auth}.py`

#### Findings

1. **`RuntimeConfig` is a four-field frozen dataclass** — `name`, `cmd`, `continue_cmd`, `env_var`. `cmd` and `continue_cmd` are **shell command strings** (not arg lists), executed via `bash -c` after the wrapper script `set -a; source /tmp/aod-env; set +a; PROMPT=$(cat); export PROMPT; exec <cmd>`. The runtime CLI must read `$PROMPT` from env (typically via `-p "$PROMPT"`) and exit 0 on success, non-zero on failure. ([`src/agent_on_demand/runtimes.py:48-53`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/runtimes.py#L48-L53), [`src/agent_on_demand/session_service/tasks.py:98-109`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/tasks.py#L98-L109))

2. **`env_var` is singular — exactly one API key per runtime** — `_write_env_file` writes `{spec.runtime.env_var}={api_key}` as the first line of `/tmp/aod-env`, then `AOD_SESSION_ID`, then `Environment.env_vars`. The api_key value is fetched via `UserRuntimeKey.get_key_for(user, runtime)` (one encrypted row per user+runtime). There is **no slot for a second runtime-config-level variable** — anything else has to come through `Environment.env_vars` (user-managed) or by extending `RuntimeConfig` itself. ([`src/agent_on_demand/session_service/provisioning.py:122-139`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L122-L139), [`src/agent_on_demand/models/auth.py:50-81`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/models/auth.py#L50-L81))

3. **`runtime_session_id` is a UUID generated server-side; per-runtime conventions vary** — `views/sessions.py:223` mints the UUID at create-time; `_write_env_file` exposes it as `AOD_SESSION_ID`. claude wires it as `--session-id` on turn 1 and `--resume <uuid>` thereafter; codex ignores it and uses `resume --last`; gemini uses `--resume` with no explicit ID. Goose's `--name <session_id>` plus `--resume` follows the claude pattern most closely. ([`src/agent_on_demand/runtimes.py:61-87`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/runtimes.py#L61-L87), [`src/agent_on_demand/views/sessions.py:223`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/views/sessions.py#L223))

4. **`AgentModel` × `MODEL_RUNTIME_MAP` enforces an N:1 model→runtime mapping** — every `AgentModel` member appears in `MODEL_RUNTIME_MAP`, which resolves to a `RUNTIMES` key. The codex precedent (`gpt-4.1`, `o3`, `o4-mini` → `"codex"`) shows the existing way to handle one runtime CLI fronting multiple models; Goose's provider-agnosticism amplifies this — the model string has to encode both provider and model (e.g. `goose-claude-sonnet-4-6`) for the mapping to retain its current shape. ([`src/agent_on_demand/runtimes.py:5-45`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/runtimes.py#L5-L45))

5. **No DB CHECK constraints — `runtime` choices are application-layer only** — `Agent.runtime` is `CharField(max_length=32, choices=...)`, `AgentSession.runtime` is a bare `CharField(max_length=32)`, `AgentVersion.runtime` likewise. Adding `"goose"` to `RUNTIMES` requires no migration for the session/version tables; the `Agent.runtime` choices update generates a cosmetic `AlterField` migration with no DB effect. ([`src/agent_on_demand/models/agents.py:18-19`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/models/agents.py#L18-L19), [`src/agent_on_demand/models/sessions.py:32`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/models/sessions.py#L32))

6. **SSE stream is byte-for-byte passthrough — no per-runtime parsing** — `stream.py` yields `AgentSessionLog.data` verbatim; `tasks.py` writes whatever `TaggingQueueWriter` captures. claude/gemini's `stream-json`, codex's `--json`, and Goose's `stream-json` are all stored and re-emitted unchanged. The output format choice is a client-side concern, not a server contract. ([`src/agent_on_demand/stream.py:15-74`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/stream.py#L15-L74), [`src/agent_on_demand/session_service/tasks.py:401-419`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/tasks.py#L401-L419))

### Track 2: Provisioning surface (install + per-runtime config)
**Researcher**: provisioning-researcher
**Scope**: `session_service/provisioning.py`, Sprite base image research, `models/environments.py`, `views/agents.py`

#### Findings

1. **All current CLIs are pre-baked into the Sprite image** — `thoughts/research/2026-04-15-sprites-platform-research.md:44-53` documents that every Sprite ships with Claude Code, Codex, and Gemini CLI pre-installed. There is no per-session install step in `provision_session`; `_install_packages` only handles user-specified `Environment.packages`. Goose is *not* in the base image today, so it needs either a new install stage or has to come in via `Environment.packages`/`setup_script`. ([`src/agent_on_demand/session_service/provisioning.py:142-157`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L142-L157))

2. **MCP writer dispatches by runtime name; each format is hand-rolled** — `_write_mcp_config` at [`provisioning.py:227-238`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L227-L238) routes to `_write_mcp_claude` (`~/.claude.json`, JSON, `mcpServers` map), `_write_mcp_codex` (`~/.codex/config.toml`, TOML, special `bearer_token_env_var` handling for `Bearer ${ENV}` headers), or `_write_mcp_gemini` (`~/.gemini/settings.json`, JSON, uses `httpUrl` not `url`, all entries get `"trust": true`). Unknown runtimes silently no-op. Goose needs a new `_write_mcp_goose` branch.

3. **`_SKILLS_ROOTS` is a per-runtime path table; `_write_skills` itself is generic** — Adding Goose to skills *would* require only a new dict entry in `_SKILLS_ROOTS`, but Goose's analogue ("recipes") is **YAML, not Markdown SKILL.md files** (see Track 3 finding 8) and requires explicit `--recipe <path>` at invocation rather than ambient discovery. The team-converged recommendation is to **omit `_SKILLS_ROOTS["goose"]` entirely for v1** — skills silently no-op via the existing `if root is None: return` guard. ([`src/agent_on_demand/session_service/provisioning.py:30-35,305-318`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L30-L35))

4. **One precedent for runtime-specific config files beyond MCP**: `_write_mcp_codex` doubles as Codex's main config writer (it's the `~/.codex/config.toml` file). Goose would follow the same pattern with `~/.config/goose/config.yaml`, holding `GOOSE_PROVIDER`, `GOOSE_MODEL`, telemetry/keyring flags, and the `extensions:` block. ([`src/agent_on_demand/session_service/provisioning.py:258-282`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L258-L282))

5. **Stage ordering: network policy applies before package installs** — `provision_session` runs `_apply_network_policy` → `_write_env_file` → `_install_packages` → `_clone_repos` → `_run_user_setup` → `_write_mcp_config` → `_write_skills`. If `networking_type=="limited"` and Goose needs to be downloaded, `github.com` (where the Goose release lives) must be in the allowed-hosts list, OR a Goose install stage must run before `_apply_network_policy`. ([`src/agent_on_demand/session_service/provisioning.py:65-72`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L65-L72))

6. **API/view layer is runtime-agnostic for MCP and skills** — `_validate_mcp_servers` ([`views/agents.py:80-102`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/views/agents.py#L80-L102)) and skills validation ([`views/agents.py:42-77`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/views/agents.py#L42-L77)) don't branch on runtime. The only runtime touch in views is the membership check `runtime not in RUNTIMES` ([`views/agents.py:238,326`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/views/agents.py#L238) and [`views/sessions.py:204`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/views/sessions.py#L204)).

### Track 3: Goose CLI external research
**Researcher**: goose-cli-researcher
**Scope**: Web research against block.github.io/goose, github.com/block/goose

#### Findings

1. **Install (Linux container)** — `sudo apt install -y bzip2 && curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | CONFIGURE=false bash`. `CONFIGURE=false` skips interactive setup. `.deb` packages also available; no official Docker image. Pin via `releases/download/v1.31.1/download_cli.sh` for reproducible builds. (Source: https://goose-docs.ai/docs/getting-started/installation/)

2. **Headless invocation** — `goose run --text "<prompt>" --output-format stream-json --mode auto --name <session_id>`. Prompt arrives via `--text/-t`, `--instructions/-i <file|->`, or `--system`. There is **no `-p` flag** — the existing `cmd` template style of `... -p "$PROMPT"` would become `... --text "$PROMPT"`. (Source: https://goose-docs.ai/docs/guides/goose-cli-commands/)

3. **Session resume** — Goose persists sessions in a shared local DB; resume in a fresh process via `goose run --text "..." --resume --name <session_id>`. The caller can choose the session name via `--name`, so our existing `runtime_session_id` UUID slots in cleanly. Auto-assigned IDs follow `YYYYMMDD_<COUNT>` if `--name` is omitted. (Source: https://goose-docs.ai/docs/guides/sessions/session-management/)

4. **Provider/model are independent and per-invocation** — `GOOSE_PROVIDER` and `GOOSE_MODEL` (env vars or CLI flags `--provider`/`--model`, or config.yaml entries). 15+ providers: anthropic, openai, google, ollama, openrouter, azure, bedrock, github_copilot, etc. **API keys are provider-standard — no `GOOSE_API_KEY`**. Anthropic backend reads `ANTHROPIC_API_KEY`; OpenAI reads `OPENAI_API_KEY`; etc. (Source: https://goose-docs.ai/docs/guides/config-files/, https://deepwiki.com/block/goose/2.2-provider-configuration)

5. **Config file** — `~/.config/goose/config.yaml` (YAML, UPPERCASE keys mirroring env var names). For containers also set `GOOSE_DISABLE_KEYRING=1` so secrets fall back to `~/.config/goose/secrets.yaml` instead of an unavailable system keyring. (Source: https://goose-docs.ai/docs/guides/config-files/)

6. **MCP = "extensions" under `extensions:` in config.yaml** — stdio entries use `type: stdio`, `cmd`, `args`, `envs`, `env_keys` (keyring lookups), `timeout`, `enabled`. Remote uses `type: streamable_http` with `uri` and `headers`. Built-ins use `type: builtin` with `bundled: true`. **No standalone mcp.json file** — all MCP config is inline in config.yaml, distinct from claude/codex/gemini's separate-file approach. (Source: https://deepwiki.com/block/goose/5.3-extension-types-and-configuration)

7. **Output format** — `--output-format stream-json` emits ndjson events as they occur. `text` and `json` (post-completion) are also options. Schema differs from claude's stream-json (Goose-specific event types); since the server is byte-passthrough this only matters to clients. `-q/--quiet` suppresses non-response output. (Source: https://goose-docs.ai/docs/guides/goose-cli-commands/)

8. **Recipes are the closest analogue to skills, but they're YAML** — Recipes have `version`, `title`, `description`, `instructions`, `parameters`, `extensions`, `sub_recipes`. Triggered with `goose run --recipe <name>`. They are **not free-form Markdown like SKILL.md** — our existing skills payload doesn't drop directly into a recipe. Either translate skill content into a recipe wrapper (instructions = skill body) or accept skills are no-op for Goose. (Source: https://goose-docs.ai/docs/guides/recipes/session-recipes/, https://github.com/block/goose/blob/main/recipe.yaml)

9. **No `--dangerously-*` flag — use `--mode auto`** — Goose's mode system: `auto` (no approvals — equivalent to claude's `--dangerously-skip-permissions`), `approve`, `smart_approve` (default), `chat`. **Prefer `--mode auto` over `GOOSE_MODE=auto`** because of [issue #3386](https://github.com/block/goose/issues/3386) where the env var was ignored for some providers in v1.0.35.

10. **Versioning** — Semver, `stable` tag in install URL tracks latest stable. Latest at time of research: **v1.31.1** (2026-04-20). Active weekly cadence; pin to a specific tag for reproducibility. (Source: https://github.com/block/goose/releases)

#### Drop-in `RuntimeConfig` sketch (per Goose-CLI track)

> Note: the team's own runtime-track and surface-track findings differ on the right shape — see Cross-Track Discoveries below. This is the *Goose-side* sketch; the system-side accommodation is open.

```python
# src/agent_on_demand/runtimes.py — illustrative, not final
RUNTIMES["goose"] = RuntimeConfig(
    name="goose",
    cmd='goose run --text "$PROMPT" --output-format stream-json --mode auto --name "$AOD_SESSION_ID"',
    continue_cmd='goose run --text "$PROMPT" --output-format stream-json --mode auto --name "$AOD_SESSION_ID" --resume',
    env_var="ANTHROPIC_API_KEY",  # if locked to anthropic backend; see strain points
)
```

Provider/model selection (`GOOSE_PROVIDER`, `GOOSE_MODEL`) and container hygiene (`GOOSE_DISABLE_KEYRING=1`, `GOOSE_TELEMETRY_ENABLED=false`) need to live somewhere — the cleanest place is a written `~/.config/goose/config.yaml`, which avoids changing `RuntimeConfig` to support multiple env vars.

#### Goose `config.yaml` shape (per Goose-CLI track)

```yaml
GOOSE_PROVIDER: "anthropic"
GOOSE_MODEL: "claude-sonnet-4-20250514"
GOOSE_MODE: "auto"
GOOSE_TELEMETRY_ENABLED: false
GOOSE_DISABLE_KEYRING: true   # forces secrets.yaml — no system keyring in container

extensions:
  developer:
    type: builtin
    name: developer
    bundled: true
    enabled: true
    timeout: 300
  # MCP servers from agent.mcp_servers translated here
```

### Track 4: API, tests, and downstream surface impact
**Researcher**: surface-researcher
**Scope**: views, models, tests (unit + e2e), README/CLAUDE.md, ui/admin
*(Task description was not preserved post-completion; findings are reproduced from the researcher's two summary messages.)*

#### Findings

1. **Runtime validation is centralized on `RUNTIMES`** — `RUNTIMES` dict at [`runtimes.py:56`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/runtimes.py#L56) is the only source of truth. `views/agents.py:238,326` and `views/sessions.py:204` both check `if req.runtime not in RUNTIMES`. Adding `"goose"` to the dict is the only change needed to pass validation.

2. **`UserRuntimeKey` adapts without code changes** — `RUNTIME_CHOICES` is built dynamically from `RUNTIMES` at [`models/auth.py:51`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/models/auth.py#L51); `makemigrations` will generate a routine `AlterField` migration. The `unique_user_runtime` constraint at `models/auth.py:63` enforces one key per `(user, runtime)` pair — relevant to the Goose-multi-provider question (see Cross-Track Discoveries).

3. **Test fixtures hardcode `"claude"` everywhere** — `tests/conftest.py:37`, `tests/test_api.py:67-84`, `tests/test_agents.py` all hardcode `runtime="claude"`. The fake sprites client (`tests/fakes/sprite.py`) is runtime-agnostic and needs no changes.

4. **`tests/test_runtimes.py` has two assertions that will fire**:
   - `test_runtimes.py:6` asserts `set(RUNTIMES) == {"claude", "codex", "gemini", "claude-oauth"}` — must add `"goose"`.
   - `test_runtimes.py:63` `test_each_runtime_has_unique_env_var` — if Goose's `env_var="ANTHROPIC_API_KEY"`, this collides with claude. Either Goose gets a synthetic env_var that the provisioning layer rewrites, or this assertion gets relaxed.

5. **E2E parameterization via `RUNTIME_MODELS` + `E2E_RUNTIMES`** — `tests/e2e/conftest.py:21-26` maps runtime → cheapest model. Adding Goose requires (a) a `RUNTIME_MODELS["goose"] = "..."` entry, (b) a seeded `UserRuntimeKey(runtime="goose")` row in the test deployment, and (c) `E2E_RUNTIMES` updated to include `"goose"`.

6. **README runtime list at lines 66-76, 80-84, 116-117** — three explicit places list the supported runtimes; all need updating.

7. **No UI/admin runtime dropdowns** beyond the choices Django auto-derives from `RUNTIMES`.

#### Goose-specific schema implications (surface-track follow-up)

Surfaced after seeing Track 3's findings; these are the architectural strain points:

- **`AgentModel` enum must encode provider+model for Goose.** Two viable shapes: (A) compound model strings like `"goose/anthropic/claude-sonnet-4-5"` parsed in the provisioning layer, or (B) one `AgentModel` member per provider+model combination (more entries, no parsing). Either way `MODEL_RUNTIME_MAP` and `AgentModel.choices()` extend, and `CreateAgentRequest.validate_model` ([`views/agents.py:116-121`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/views/agents.py#L116-L121)) will reject any Goose model string not in `AgentModel.values()`.

- **`UserRuntimeKey` is one-key-per-(user,runtime).** A single `"goose"` runtime row is ambiguous for a user who wants to use Goose with both Anthropic and OpenAI. Three shapes: (1) restrict each user's Goose to one provider (one key, encoded in the model string), (2) namespace runtime as `goose-anthropic`/`goose-openai`, (3) introduce a `provider` field on `UserRuntimeKey` (schema change). Option (1) is simplest; option (2) gives flexibility without schema change.

- **`test_each_runtime_has_unique_env_var`** at `tests/test_runtimes.py:63` codifies the "one runtime ↔ one env_var" assumption. Whatever Goose strategy is chosen has to explicitly engage with this test.

## Cross-Track Discoveries

Findings that emerged from connections between tracks:

- **The "single env_var" assumption ripples through 4 layers.** Track 1 found `RuntimeConfig.env_var` is singular; Track 3 confirmed Goose needs `GOOSE_PROVIDER` + `GOOSE_MODEL` + provider-native key; Track 2 noted `_write_mcp_codex` already writes a non-MCP config file (the codex pattern of "MCP writer doubles as runtime config writer"); Track 4 identified the test that codifies env_var uniqueness. **The path of least resistance**: write a `~/.config/goose/config.yaml` containing `GOOSE_PROVIDER`, `GOOSE_MODEL`, telemetry/keyring flags, and `extensions:` (combining what would otherwise be `_write_mcp_goose` + `_write_goose_config` into one writer, mirroring codex). Then `RuntimeConfig.env_var` only carries the provider's native API key, no contract change.

- **Goose's "recipes" are YAML — our `SkillSpec.content` is Markdown.** `_write_skills` at `provisioning.py:305-318` writes `SKILL.md` files; that doesn't match Goose. Two options: (a) wrap the SKILL.md content as the `instructions:` field of a recipe YAML and write it under `~/.config/goose/recipes/<name>.yaml`, or (b) declare skills no-op for Goose for v1 and revisit. Option (a) is small but requires teaching `_write_skills` (or a `_write_recipes_goose` sibling) about runtime-specific serialization.

- **Sprite image vs. install stage decision is forced by network policy.** Track 2's finding that `_apply_network_policy` runs before `_install_packages` means that if Goose is installed at provision-time AND the environment uses `networking_type="limited"`, the allowed-hosts list must include `github.com` (release host) and `objects.githubusercontent.com`. The cleanest fix is to bake Goose into the Sprite base image (out-of-band of this repo), eliminating the install stage entirely. Track 3 gives the install command; the question is who owns that change.

- **Session-id semantics align cleanly.** Track 1 noted claude/gemini/codex have three different resume conventions; Track 3 confirmed Goose's `--name <id>` + `--resume` matches claude's pattern. The existing `runtime_session_id` UUID drops in as `--name "$AOD_SESSION_ID"` with no contract changes.

## Code References

| File | Tracks | Findings | Link |
|------|--------|----------|------|
| `src/agent_on_demand/runtimes.py:5-87` | 1, 4 | RuntimeConfig shape; AgentModel enum; MODEL_RUNTIME_MAP; RUNTIMES dict | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/runtimes.py#L5-L87) |
| `src/agent_on_demand/session_service/tasks.py:98-109` | 1 | `build_turn_command` — wrapper script semantics | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/tasks.py#L98-L109) |
| `src/agent_on_demand/session_service/provisioning.py:30-35` | 2 | `_SKILLS_ROOTS` — per-runtime skills dir | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L30-L35) |
| `src/agent_on_demand/session_service/provisioning.py:122-139` | 1, 2 | `_write_env_file` — single-env-var injection | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L122-L139) |
| `src/agent_on_demand/session_service/provisioning.py:227-302` | 2 | `_write_mcp_config` and per-runtime writers | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L227-L302) |
| `src/agent_on_demand/models/agents.py:18-19` | 1, 4 | `Agent.model`/`Agent.runtime` field definitions | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/models/agents.py#L18-L19) |
| `src/agent_on_demand/models/auth.py:50-81` | 1, 4 | `UserRuntimeKey` — one key per (user, runtime) | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/models/auth.py#L50-L81) |
| `src/agent_on_demand/views/agents.py:80-102,116-121,238,326` | 4 | Runtime validation and model validation in views | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/views/agents.py#L80-L102) |
| `src/agent_on_demand/views/sessions.py:204,223` | 1, 4 | Runtime guard + UUID generation | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/views/sessions.py#L204) |
| `src/agent_on_demand/stream.py:15-74` | 1 | SSE replay — runtime-agnostic byte passthrough | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/stream.py#L15-L74) |
| `tests/test_runtimes.py:6,63` | 4 | Set-equality + unique-env-var assertions to update | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/tests/test_runtimes.py#L6) |
| `tests/e2e/conftest.py:21-26` | 4 | `RUNTIME_MODELS` + `E2E_RUNTIMES` parameterization | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/tests/e2e/conftest.py#L21-L26) |

## Architecture Insights

- **Server is intentionally a dumb pipe between an HTTP request and a CLI on a Sprite.** No output parsing, no per-runtime business logic in views, no per-runtime DB constraints. The runtime contract is held in a single dataclass and a small dispatch table. This makes adding "another CLI" cheap mechanically; it also means the contract has to evolve when a new CLI doesn't match the implicit assumptions baked into the dataclass.

- **The codex precedent is the right model for Goose.** Codex already has multiple `AgentModel` strings → one `RUNTIMES["codex"]`. Goose extends that to many more (provider × model combinations) and adds a richer config file, but it's the same shape.

- **`provisioning.py` is the natural place for runtime-specific knowledge.** Views, tasks, and stream endpoints stay generic; per-runtime quirks live in `_write_mcp_<runtime>` and `_SKILLS_ROOTS`. Goose's `_write_goose_config` would fit alongside.

- **The "stages" abstraction in `provision_session` is well-suited to the planned stage-events work** ([`thoughts/plans/2026-04-21-provision-stage-events.md`](thoughts/plans/2026-04-21-provision-stage-events.md)). A new "install_goose" stage would emit its own event automatically.

## Historical Context

- `thoughts/research/2026-04-15-sprites-platform-research.md` — confirms claude/codex/gemini are baked into the Sprite base image and explains "no install step needed for our target CLIs."
- `thoughts/research/2026-04-18-session-backend-abstraction.md` — earlier thinking on runtime abstraction.
- `thoughts/plans/2026-04-21-provision-stage-events.md` — the in-flight stage-events refactor that any new install stage would integrate with.

## Recommended approach (team-converged)

Through cross-track messaging the researchers converged on a concrete design that requires **no `RuntimeConfig` shape change, no schema migration**, and isolates Goose's quirks to two well-defined seams.

1. **One `RUNTIMES["goose"]` entry, model string encodes provider.** Pattern: `goose/anthropic/claude-sonnet-4-20250514`, `goose/openai/gpt-4o`, etc., all mapping to `"goose"` in `MODEL_RUNTIME_MAP`. Same precedent as codex's `gpt-4.1`/`o3`/`o4-mini` → `"codex"`. One `UserRuntimeKey` row per (user, "goose"). The provisioning layer parses the model string at spec-build time to derive provider and model id.

2. **`GOOSE_PROVIDER`/`GOOSE_MODEL` go in a written `~/.config/goose/config.yaml`, not in `RuntimeConfig` or env vars** — exactly mirroring how `_write_mcp_codex` writes `~/.codex/config.toml`. A new `_write_goose_config(sprite, spec, mcp_servers)` writes the YAML with provider/model/mode/telemetry/keyring settings AND the `extensions:` block (MCP servers) in a single pass. This keeps `RuntimeConfig.env_var` singular (no contract change) and avoids two-pass merge problems.

3. **`env_var="ANTHROPIC_API_KEY"` (provider-standard, shared with claude). Relax the uniqueness test, don't invent a synthetic `GOOSE_API_KEY`.** The `claude-oauth` runtime already established the "same provider, different auth surface" precedent (claude → `ANTHROPIC_API_KEY`, claude-oauth → `CLAUDE_CODE_OAUTH_TOKEN`). Goose-with-Anthropic extends this. The `test_each_runtime_has_unique_env_var` invariant guards *accidental* collisions; intentional shared-provider runtimes should be permitted with an explanatory comment. **Note**: this means a user wanting both claude and goose-with-anthropic configures `ANTHROPIC_API_KEY` twice (once per runtime row in `UserRuntimeKey`) — a UX wart worth flagging at product level, but it doesn't block implementation.

4. **`GOOSE_DISABLE_KEYRING`/`GOOSE_TELEMETRY_ENABLED` live in `config.yaml`, not in process env.** Avoids the `RuntimeConfig.static_env` extension that was briefly considered. The CLI also explicitly passes `--mode auto`/`--provider`/`--model` (not relying on env vars alone) because of [block/goose#3386](https://github.com/block/goose/issues/3386).

5. **Install Goose into the Sprite base image (preferred) — fall back to a per-session install stage if not.** If installed at provision time: new `_install_goose_runtime(sprite)` stage runs after `_apply_network_policy`, conditioned on `spec.runtime.name == "goose"`. Limited network policies must allow `github.com` and `objects.githubusercontent.com`. Install command: `apt-get install -y -qq bzip2 && curl -fsSL https://github.com/block/goose/releases/download/<pinned-tag>/download_cli.sh | CONFIGURE=false bash`.

6. **Skills are no-op for Goose v1.** Goose recipes are structurally incompatible with the existing `SkillSpec` (recipes are YAML with `instructions`/`parameters`/`sub_recipes`; SKILL.md is free-form Markdown) and require explicit `--recipe <path>` at invocation time rather than ambient discovery. Omit the `_SKILLS_ROOTS["goose"]` entry; the existing `if root is None: return` guard at [`provisioning.py:308-309`](https://github.com/ravi-hq/agent-on-demand/blob/919a4d4/src/agent_on_demand/session_service/provisioning.py#L308-L309) handles this silently.

7. **MCP server env vars use Goose's `envs:` (plaintext in config.yaml), not `env_keys:` + secrets.yaml.** Matches the threat model used today by claude (`.claude.json`) and codex (`config.toml`) — secrets are encrypted at rest in our DB; the Sprite filesystem is ephemeral and isolated. The `env_keys:`/`secrets.yaml` path stays available as a future upgrade.

## Implementation checklist

Concrete files/lines to touch, organized by track:

**`src/agent_on_demand/runtimes.py`**
- Add `AgentModel` members for each supported `(provider, model)` pair, e.g. `GOOSE_ANTHROPIC_SONNET_4 = "goose/anthropic/claude-sonnet-4-20250514"`.
- Add each new member to `MODEL_RUNTIME_MAP` mapping to `"goose"`.
- Add `RUNTIMES["goose"] = RuntimeConfig(name="goose", cmd='goose run --text "$PROMPT" --output-format stream-json --mode auto --name "$AOD_SESSION_ID"', continue_cmd=<same + --resume>, env_var="ANTHROPIC_API_KEY")`.

**`src/agent_on_demand/session_service/specs.py`**
- Add `model: str | None = None` to `SessionSpec` (frozen dataclass). Existing callers pass nothing; only `_write_goose_config` reads it. This is the minimal honest change — the model genuinely determines session configuration for Goose.

**`src/agent_on_demand/session_service/tasks.py`**
- In `_build_spec_for_session` (around line 225), add `model=session.agent.model` to the `SessionSpec(...)` call. The `Agent` row is already fetched.

**`src/agent_on_demand/session_service/provisioning.py`**
- Add `_install_goose_runtime(sprite)` stage; call from `provision_session` after `_apply_network_policy` when `spec.runtime.name == "goose"` (skip if Sprite image bakes Goose in).
- Add `_write_goose_config(sprite, spec, mcp_servers)` writing `~/.config/goose/config.yaml` with provider/model/mode/telemetry/keyring + `extensions:` block. Parse `spec.model` (`goose/<provider>/<model_id>`) to derive `GOOSE_PROVIDER` and `GOOSE_MODEL`.
- Update `_write_mcp_config` to dispatch to `_write_goose_config` for `"goose"`, OR call it from `provision_session` directly (since config.yaml is needed even with zero MCP servers, unlike claude/codex/gemini).
- **Do not** add `"goose"` to `_SKILLS_ROOTS`.

**`tests/test_runtimes.py`**
- Update `test_all_runtimes_defined` (line 6): add `"goose"` to set equality.
- Update `test_each_runtime_has_unique_env_var` (line 63): relax with comment explaining intentional Anthropic-key sharing.
- Add `test_goose_runtime_uses_name_and_resume` (mirror of `test_claude_runtime_uses_session_id_and_resume` at line 54).
- Add coverage in `test_turn_command_selects_by_mode` for the `"goose"` runtime.

**`tests/e2e/conftest.py:21-26`**
- Add `"goose": "<cheap goose model id>"` to `RUNTIME_MODELS`.
- Update `E2E_RUNTIMES` to include `"goose"`.
- Seed `UserRuntimeKey(runtime="goose")` row in the e2e test deployment.

**`tests/conftest.py`, `tests/test_agents.py`, `tests/test_api.py`, `tests/test_session_service.py`, `tests/test_tasks.py`**
- Add Goose-runtime cases for create/update/list/version paths to mirror existing claude coverage.
- The fake Sprites client (`tests/fakes/sprite.py`) is runtime-agnostic — no changes.

**`README.md`** — update runtime lists at lines 66-76, 80-84, 116-117. **`CLAUDE.md`** — add Goose to the runtime callout if applicable.

**Sprite base image (out of this repo)** — strongly preferred to bake Goose in, eliminating the `_install_goose_runtime` stage. Pin to a specific Goose release.

## Remaining open questions

These are product/UX questions, not blocking research findings:

1. **UX of duplicate `ANTHROPIC_API_KEY` storage.** A user using both claude and goose-with-anthropic must configure the same Anthropic key twice (once per `UserRuntimeKey` row). Acceptable for v1, but worth a follow-up to consider key-sharing semantics.

2. **Which Goose providers to support in v1.** The model-string encoding scales to all 15+ Goose providers, but each new provider added to `AgentModel` requires its own `(provider, model)` entries. Recommend launching with anthropic + openai and growing on demand.

3. **Goose version pinning policy.** Track 3 recommends pinning the Sprite-image install to a specific tag (e.g. `v1.31.1`) for reproducibility. Choose a cadence for upgrades.

## Related Research

- [`thoughts/research/2026-04-18-session-backend-abstraction.md`](2026-04-18-session-backend-abstraction.md)
- [`thoughts/research/2026-04-15-sprites-platform-research.md`](2026-04-15-sprites-platform-research.md)
- [`thoughts/plans/2026-04-21-provision-stage-events.md`](../plans/2026-04-21-provision-stage-events.md)
