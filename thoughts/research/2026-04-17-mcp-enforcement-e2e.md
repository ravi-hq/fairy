---
date: 2026-04-17T09:42:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 4c3b9cc1e5e7871436bdec48c063c76d47db30a0
branch: agent-tool-enforcement
repository: ravi-hq/fairy
topic: "E2E test suite verifying coding agents respect Agent.mcp_servers + mcp_toolset"
tags: [research, team-research, e2e, mcp, claude-cli, codex, gemini, enforcement]
status: complete
method: agent-team
team_size: 5
tracks: [fairy-wiring-audit, runtime-gating, test-infra, matrix-design, managed-agents-parity]
last_updated: 2026-04-17
last_updated_by: Claude Code
---

# Research: E2E test suite verifying MCP servers + mcp_toolset are respected by coding agents

**Date**: 2026-04-17
**Researcher**: Claude Code (team-research)
**Git Commit**: [`4c3b9cc`](https://github.com/ravi-hq/fairy/commit/4c3b9cc1e5e7871436bdec48c063c76d47db30a0)
**Branch**: `agent-tool-enforcement`
**Repository**: ravi-hq/fairy
**Method**: Agent team (5 specialist researchers)

## Research Question

Build an e2e test suite that verifies `Agent.mcp_servers` + `mcp_toolset` are respected by the coding agent runtimes (claude, codex, gemini). Fairy's MCP shape is modeled on Anthropic's Managed Agents API (`mcp_servers` at agent-level, `vault_ids` at session-level). Each runtime has its own translation, and tests must prove the translation actually reaches the running agent and gates tool availability.

## Summary

This effort is the MCP analog of the tool-enforcement work landed in commit [`4c3b9cc`](https://github.com/ravi-hq/fairy/commit/4c3b9cc1e5e7871436bdec48c063c76d47db30a0). The gap structure is the same shape as what that commit closed for built-in tools: **Fairy validates `mcp_toolset` entries but only the `mcp_server_name` field is actually wired through to a runtime, and only for claude's allowlist path.** `mcp_toolset.configs[]` (per-tool filter) and `mcp_toolset.default_config.enabled` (server-level toggle) are stored, returned, and ignored end-to-end (`src/fairy/views.py:485-498`, `src/fairy/sprites_exec.py:286-321`).

Per-runtime, the story gets more complicated than for built-in tools:

- **Claude**: `--disallowedTools` **silently ignores MCP tool names** in `-p` (headless) mode — upstream Claude Code issue #12863, closed-as-not-planned. `--allowedTools "mcp__server__*"` wildcard also silently fails (#13077). The only working claude-side deny path is file-based `permissions.deny` in `settings.json` — Fairy has no writer for that today. The allow path via `--tools "mcp__<server>"` works correctly and is what `_tool_flags_claude` already emits.
- **Codex**: `[mcp_servers.<name>]` config supports `enabled_tools` (allowlist), `disabled_tools` (denylist applied after allowlist), `enabled` (whole-server toggle), `required` (fail if unreachable). Fairy's `_build_mcp_codex` only emits `url` + `bearer_token_env_var` + `required = true` — none of the allow/deny keys are wired.
- **Gemini**: Policy Engine TOML (`~/.gemini/policies/*.toml`) uses **single-underscore** `mcp_{server}_{tool}` naming (NOT double like claude/codex). `includeTools`/`excludeTools` on the `mcpServers` entry in `settings.json` also works. Neither is wired in Fairy today.

The Managed Agents vault model (agent-level servers + session-level `vault_ids` for credentials) is **out of scope** for this effort — Anthropic's vaults API is not GA, Fairy has no vault resource, and inline headers on `Agent.mcp_servers` are sufficient to prove "a bearer token reaches the MCP server" end-to-end. Flagged as a future security-hardening item: `Agent.mcp_servers[].headers` is plaintext at rest and returned in API responses, inconsistent with how Fairy treats `env_vars` or session tokens.

For the test MCP server, the recommendation is **Option B: a Fairy-hosted `/test-mcp` endpoint** using the `mcp` Python SDK. Sprites have unrestricted outbound network by default, so they can reach Fairy's public URL. Three deterministic tools (`signal_tool`, `echo`, `dangerous_tool`) drive simple grep assertions. Estimated cost of a full matrix run: $0.30–0.70 at haiku/o4-mini/gemini-flash rates.

**Critical implication for the implementation plan**: the draft matrix skeleton includes `test_mcp_deny_specific_tool` and `test_mcp_deny_entire_server`, both of which **would not pass against Fairy today** — the enforcement doesn't exist. A pre-test wiring phase (mirroring how `4c3b9cc` closed the tool-enforcement gap) is required before the deny-polarity tests can meaningfully land. The allow-polarity tests and the "no server declared → no mcp_* tools invoked" test can land against current Fairy.

## Research Tracks

### Track 1: Current Fairy MCP wiring audit
**Researcher**: `fairy-mcp-auditor`
**Scope**: `src/fairy/models.py`, `src/fairy/views.py`, `src/fairy/sprites_exec.py`, `src/fairy/runtimes.py`, existing unit + e2e tests

#### Findings:

1. **`mcp_servers` entry shape** — Validated in `_validate_mcp_servers` ([`views.py:501-523`](../../src/fairy/views.py)). Required: `name` (string, unique within array). Optional: `type` (`"url"` | `"stdio"`, defaults to `"url"`). For `type="url"`: `url` required. For `type="stdio"`: `command` required. Optional on all: `headers` (dict), `args` (list), `env` (dict). Max 20 servers per agent. No validation of header values, URL format, or command path.

2. **`mcp_toolset` entry shape is minimal** — `_validate_tools` at [`views.py:485-498`](../../src/fairy/views.py) requires only `mcp_server_name` on `mcp_toolset` entries. No validation that the referenced server name exists in the agent's `mcp_servers` array — a dangling reference is accepted silently. The `name` field (tool-level filter), `configs[]`, and `default_config.enabled` are NOT validated or required and are **never read by any runtime translation path**. These only have semantics on `agent_toolset_20260401` entries.

3. **Runtime-specific MCP emission** ([`sprites_exec.py`](../../src/fairy/sprites_exec.py)):
   - Claude ([`:120-140`](../../src/fairy/sprites_exec.py)): writes `/tmp/mcp.json` heredoc with `{"mcpServers": {name: {type, url, headers}}}`. Headers pass through verbatim.
   - Codex ([`:170-210`](../../src/fairy/sprites_exec.py)): writes `~/.codex/config.toml` with `[mcp_servers.<name>]` blocks. Only `Authorization: Bearer ${VAR}` headers are supported — extracted to `bearer_token_env_var = "VAR"`. All other headers silently dropped.
   - Gemini ([`:213-233`](../../src/fairy/sprites_exec.py)): writes `~/.gemini/settings.json` with `{"mcpServers": {name: {httpUrl, trust: true, headers}}}`. Headers pass through verbatim.
   - `mcp_toolset` entries **do not affect MCP config emission** in any runtime.

4. **CLI flags** — Claude/claude-oauth ([`:258-264`, `:293-321`](../../src/fairy/sprites_exec.py)): appends `--mcp-config /tmp/mcp.json --strict-mcp-config`. `mcp_toolset` contributes `mcp__<server>` to `--tools` allowlist only when `default_config.enabled=False` on an `agent_toolset_20260401` entry; when `default_config.enabled=True`, `mcp_toolset` is ignored. Codex: no MCP CLI flags. Gemini: no MCP CLI flags.

5. **`--continue` path re-emits MCP config** — `send_prompt` at [`views.py:378-395`](../../src/fairy/views.py) fetches the agent's current `mcp_servers`, converts to specs, and passes them to `build_wrapper_script(continue_session=True, mcp_servers=..., tools=...)`. `build_wrapper_script` emits the MCP section and flags unconditionally, so MCP config is re-written every turn. Correct behavior since sprite filesystem is overwritten each turn.

6. **Test coverage gaps** — What IS covered: `tests/test_tools_mcp.py` covers unit-level MCP config generation (`TestWrapperScriptMcp`), CRUD validation (`TestAgentMcpValidation`), and session+MCP integration with mock sprites. `tests/e2e/test_agents.py::TestAgentTools` covers API-level CRUD validation for `mcp_servers`. What is NOT covered: (a) no e2e test that spawns a real session with an MCP server and verifies the runtime actually connects and calls tools; (b) no test that `mcp_toolset.mcp_server_name` referencing a non-existent server is rejected; (c) no codex `bearer_token_env_var` round-trip test; (d) `TestMcpToolset` e2e class is `@skip` pending a live MCP test server (tracked in [`thoughts/plans/2026-04-16-agent-tool-enforcement.md`](../plans/2026-04-16-agent-tool-enforcement.md)).

7. **`mcp_toolset` runtime influence is one line of code** — Only `_tool_flags_claude` at [`sprites_exec.py:286-321`](../../src/fairy/sprites_exec.py) reads `mcp_toolset` entries, and only in the allowlist path (when an `agent_toolset_20260401` sets `default_config.enabled=False`). `_codex_top_level_keys` ([`sprites_exec.py:143-167`](../../src/fairy/sprites_exec.py)) and `_tool_files_gemini` ([`sprites_exec.py:342-392`](../../src/fairy/sprites_exec.py)) never consult `mcp_toolset`.

8. **Deferred test** — `TestMcpToolset` referenced in [`thoughts/plans/2026-04-16-agent-tool-enforcement.md:63`](../plans/2026-04-16-agent-tool-enforcement.md) was marked "`@skip` pending a live MCP test server — separate future work." No other MCP `@skip`/`@xfail` markers exist.

---

### Track 2: Per-runtime MCP gating surfaces
**Researcher**: `runtime-gating-researcher`
**Scope**: Claude Code, Codex, Gemini CLI docs + source; stream-event shapes

#### Findings:

9. **Claude `--disallowedTools` silently ignores MCP tool names in `-p` mode** — Upstream Claude Code issue **#12863**, closed-as-not-planned. Built-in tools (`Bash`, `Read`, `WebFetch`, etc.) are correctly blocked, but `mcp__server__tool` names passed to `--disallowedTools` have no effect. This is an upstream limitation, not a Fairy bug.

10. **`--allowedTools "mcp__server__*"` wildcard also silently fails** — Upstream Claude Code issue **#13077**. Only explicit per-tool listing works in `--allowedTools`. Server-wide `--tools "mcp__<server>"` allowlist DOES work.

11. **Claude file-based deny path** — The only working deny mechanism for MCP tools in headless claude is `permissions.deny` rules in `~/.claude/settings.json` (or a custom path via `--config-dir`). Example: `{"permissions": {"deny": [{"tool": "mcp__server__tool"}]}}`. Fairy has no writer for this file — the gemini `_tool_files_gemini` TOML writer is the closest pattern.

12. **Codex MCP config keys** ([config.toml](https://github.com/openai/codex)):
    - `enabled_tools = ["a", "b"]` — explicit allowlist
    - `disabled_tools = ["x"]` — denylist applied after allowlist
    - `enabled = true|false` — whole-server toggle
    - `required = true` — session fails if server unreachable
    - `bearer_token_env_var = "VAR"` — auth by env-var indirection (only auth shape Fairy supports for codex today)
    - `codex resume` reloads `config.toml` every invocation — same as init

13. **Gemini per-MCP-tool enforcement** — Two mechanisms:
    - `~/.gemini/policies/*.toml` Policy Engine rules. Tool names use **single-underscore** `mcp_{server}_{tool}`, NOT double. This differs from Claude/Codex. Rules shape: `[[policies]] name = "mcp_server_tool" action = "deny"`.
    - `includeTools` / `excludeTools` on the `mcpServers` entry in `settings.json`.
    - `--resume` re-reads `settings.json` on each invocation.
    - Single-underscore naming means MCP server aliases with underscores (e.g. `test_server`) will misparse in the Policy Engine.

14. **Stream-event shapes for MCP tool calls**:
    - **Claude**: Regular `tool_use` block in `assistant` message content. `name` field contains the full `mcp__<server>__<tool>` string. Example: `{"type":"tool_use","name":"mcp__testmcp__signal_tool","input":{...}}`. MCP servers appear in the `system` init event's `mcp_servers` array.
    - **Codex**: `item.started` / `item.completed` events with `item.type = "mcp_tool_call"`. Fields: `item.server` and `item.tool`. Already parsed at [`tests/e2e/test_agent_tools.py:89-92`](../../tests/e2e/test_agent_tools.py). Reconstruct name as `mcp__{server}__{tool}`.
    - **Gemini**: `tool_use` event with `tool_name` field containing `mcp__<server>__<tool>` (stream output uses double-underscore even though Policy Engine uses single — confirmed separately).

15. **Auth passthrough** — Claude: arbitrary `headers` dict in `mcpServers` entry, passed verbatim. Codex: only `bearer_token_env_var` — the env var must be exported by the wrapper script before invocation. Gemini: arbitrary `headers` dict in `mcpServers` entry, passed verbatim. On 401: claude emits no structured event (tool just fails with error result); codex surfaces as MCP initialization error on session start; gemini surfaces as tool failure.

16. **Server name uniqueness** — No runtime cares about agent-to-agent collisions. Names are scoped within a single wrapper script / session.

---

### Track 3: Test MCP server infrastructure
**Researcher**: `mcp-test-infra-researcher`
**Scope**: Sprites networking, MCP SDK options, auth testing, CI-friendliness

#### Findings:

17. **Sprites have unrestricted outbound network by default** — Network policies (DNS-based allow/deny rules) are opt-in only. A sprite session can reach Fairy's public URL without any tunneling. This is the key enabler for Option B (Fairy-hosted test MCP endpoint).

18. **Fairy supports `stdio` MCP type end-to-end** — `_validate_mcp_servers` at [`views.py:501-523`](../../src/fairy/views.py) accepts `type="stdio"`, and all three runtime writers handle it. But stdio can't test bearer-auth headers, so it's useful only for non-auth tests.

19. **Decision matrix** (from test-infra research):

| Option | Determinism | Network Feasibility | Impl Cost | Auth Testing | CI Friendly |
|---|---|---|---|---|---|
| A. Public hosted (deepwiki/context7) | Low — non-deterministic | High | None | No | Low — rate limits |
| **B. Fairy-hosted `/test-mcp`** | **High** | **High** | Medium (~100 LOC) | **Yes** | **High** |
| C. Conftest-spawned FastMCP subprocess | High | Low — needs tunnel | Medium | Yes (local) | Low — tunneling flaky |
| D. stdio server inside Sprite | High | N/A | Low | No (no HTTP) | Medium |
| E. Hybrid stdio + public HTTP | Medium | Mixed | High | Partial | Low |

20. **Recommendation: Option B (Fairy-hosted `/test-mcp`)** — Django view implementing MCP Streamable HTTP protocol via the `mcp` Python SDK (`FastMCP` class). Three deterministic tools: `signal_tool(token: str) -> f"MCP_SIGNAL_{token}"`, `echo(msg: str) -> msg`, `dangerous_tool() -> "SHOULD_NOT_BE_CALLED"`. Optional bearer-token check via `MCP_TEST_TOKEN` env var. Gated behind `DEBUG or TESTING` so it's never active in prod.

21. **Cost estimate** — 3 runtimes × 4–5 scenarios = ~12–15 sessions/run at haiku/o4-mini/gemini-flash rates ≈ **$0.30–0.70 per full run**. Under the $1.00 ceiling.

22. **Conftest fixture shape**:
    ```python
    @pytest.fixture(scope="session")
    def mcp_test_url():
        url = os.environ.get("MCP_TEST_URL", "")
        if not url:
            pytest.skip("MCP_TEST_URL not set")
        return url
    ```

---

### Track 4: E2E matrix design & test scaffolding
**Researcher**: `matrix-designer`
**Scope**: Pattern-mirror `tests/e2e/test_agent_tools.py`, design scenarios + parsers + Makefile/pyproject wiring

#### Findings:

23. **Proposed matrix** (3 runtimes × 4 scenarios + 1 multi-turn = ~14 sessions):

    | Test class | Scenario | Runtimes | Enforceable today? |
    |---|---|---|---|
    | `TestMcpServerToolInvocable` | server declared, no restriction → tool invoked | claude, codex, gemini | **Yes** |
    | `TestMcpDenySpecificTool` | `mcp_toolset.configs` denies one tool | claude, codex*, gemini | **No** — needs wiring |
    | `TestMcpDenyEntireServer` | `default_config.enabled=False` → whole server blocked | claude, codex, gemini | **Partial** — claude allowlist path only |
    | `TestNoMcpServerNoMcpTool` | no `mcp_servers` → no `mcp__*` tools invoked | claude, codex, gemini | **Yes** |
    | `TestMcpMultiTurnPersistence` | deny persists across `POST /prompt` | claude only | Depends on deny path |

24. **Parsers fully derivable today** — Claude and Codex parsers are straight extensions of the existing `_parse_claude_tool_names` / `_parse_codex_tool_names` in [`tests/e2e/test_agent_tools.py`](../../tests/e2e/test_agent_tools.py) — filter on `name.startswith("mcp__")` for claude, and the `mcp_tool_call` item type is already parsed at lines 89–92. Gemini parser filters on `tool_name.startswith("mcp__")` in stream `tool_use` events.

25. **Markers**: Add `mcp_matrix` to `[tool.pytest.ini_options].markers` in `pyproject.toml`, matching the existing `tool_matrix` marker.

26. **Makefile target**:
    ```makefile
    test-e2e-mcp:
        uv run pytest tests/e2e/test_mcp_enforcement.py -v -m "mcp_matrix"
    ```

27. **Fast-path for iteration**: `E2E_RUNTIMES=claude make test-e2e-mcp` = 4 sessions, ~$0.05.

28. **Skeleton gaps that became code-changes**: The `_mcp_toolset_deny_one_tool()` helper uses `configs: [{"name": ..., "enabled": false}]` — a shape the Fairy validator accepts but silently discards. The `_mcp_toolset_deny_server()` helper uses `default_config: {"enabled": false}` — same story. Both tests would not pass today because Fairy has no enforcement path for either.

---

### Track 5: Managed Agents API parity & vault scope
**Researcher**: `managed-agents-parity-researcher`
**Scope**: Anthropic vault model, Fairy's current auth plumbing, scope decision

#### Findings:

29. **Scope decision: punt vaults; use inline `headers` for this effort** — Anthropic's vault API endpoint returns 404 (not GA). Fairy has no vault model. Inline headers on `Agent.mcp_servers` are sufficient to prove "a bearer token reaches the MCP server" end-to-end. The scope line: *"E2E MCP tests use inline `headers` on agent-level `mcp_servers`. A future `vaults` model (analogous to Anthropic's session-time `vault_ids`) will supersede this; auth-rotation tests are deferred until then."*

30. **Managed Agents split** — Agent-level `mcp_servers[]: {type, name, url}` carries no auth. Session-level `vault_ids[]` references vault resources containing credentials. Credential types: `mcp_oauth` (OAuth + refresh), `mcp_bearer` (static token). Vault fields are write-only — tokens cannot be read back via the API. The split is intentional: "what servers exist" is reusable, "how to auth to them" is per-session.

31. **Fairy is inconsistent with its own crypto pattern** — `Agent.mcp_servers` is an unencrypted `JSONField` ([`models.py:223`](../../src/fairy/models.py)). `_serialize_agent` at [`views.py:595-613`](../../src/fairy/views.py) returns full `mcp_servers` blob including `headers` containing bearer tokens. Compare to `Environment.env_vars` (encrypted via [`crypto.py:14-19`](../../src/fairy/crypto.py), never returned) and `SessionResource.encrypted_token`. User-scoping mitigates exposure (only the agent owner can read), but plaintext-at-rest + plaintext-in-response is a hardening gap.

32. **E2E auth coverage (in-scope)**:
    - `test_mcp_tool_invoked_with_valid_auth` — valid sentinel token → MCP tool appears in stream
    - `test_mcp_tool_absent_with_invalid_auth` — invalid header → MCP tool does NOT appear (absence assertion only — no runtime emits a structured auth-failure event)
    - Out of scope: `vault_ids`, OAuth + refresh, `session.error` structured events, encrypted-at-rest headers.

33. **Auth in test fixtures**: Use fake/sentinel tokens only. Fairy returns `headers` in API responses, so any real token in a test fixture will land in CI logs.

## Cross-Track Discoveries

These findings only emerged from connections between tracks:

- **The MCP enforcement gap is the direct analog of the tool enforcement gap closed by [`4c3b9cc`](https://github.com/ravi-hq/fairy/commit/4c3b9cc1e5e7871436bdec48c063c76d47db30a0)** (Track 1 + Track 2). `mcp_toolset.configs` and `default_config.enabled` are validated but not wired. For each runtime, the fix path mirrors what `4c3b9cc` did for `agent_toolset_20260401`: add a new writer function (or extend existing MCP writers) to emit runtime-specific allow/deny config from `mcp_toolset` entries.

- **Claude's headless MCP deny path requires a settings.json writer Fairy doesn't have** (Track 1 + Track 2). The `_tool_files_*` pattern introduced in `4c3b9cc` for gemini Policy Engine is the template — a new `_tool_files_claude_permissions` function that writes `~/.claude/settings.json` with `permissions.deny` rules. The claude-CLI test for `TestMcpDenySpecificTool` and `TestMcpDenyEntireServer` cannot pass without this writer.

- **Codex has the richest built-in MCP enforcement surface, but Fairy uses the least of it** (Track 1 + Track 2). Codex `[mcp_servers.<name>]` supports `enabled_tools` + `disabled_tools` + `enabled` + `required`. Fairy's `_build_mcp_codex` only emits `url` + `bearer_token_env_var` + `required`. Adding the allow/deny keys to `_build_mcp_codex` (keyed on `mcp_toolset.configs`) would unlock codex per-tool deny without new writer functions.

- **Gemini server names must avoid underscores** (Track 2 + Track 3 + Track 4). Gemini Policy Engine uses single-underscore `mcp_{server}_{tool}` naming, so an alias like `test_server` misparses. The Fairy-hosted `/test-mcp` server should use a simple alias like `testmcp` (no underscore) in fixtures to avoid this.

- **The "deny" polarity tests are blocked on Fairy wiring, not on the test server or the runtimes** (Track 1 + Track 4). Options for the implementation plan: (a) ship e2e tests for the allow/absence polarity only now and xfail/skip deny tests pending wiring; (b) precede the e2e suite with a wiring phase analogous to `4c3b9cc` that adds per-runtime MCP deny writers; (c) scope this effort exclusively to the wiring + e2e bundle. Recommendation: (c) — the value of e2e tests is proving the wiring, so splitting them defeats the purpose.

- **`Agent.mcp_servers[].headers` plaintext exposure is orthogonal to this effort but should be tracked** (Track 1 + Track 5). Not a blocker; flag as a hardening item. The fix (encrypt + strip from serialization) mirrors the `env_vars` pattern.

## Code References

| File | Lines | Finding | Notes |
|------|-------|---------|-------|
| `src/fairy/models.py` | 222–223 | `Agent.tools` + `mcp_servers` are plain JSONFields | Tools wired by `4c3b9cc`; MCP still partial |
| `src/fairy/views.py` | 485–498 | `_validate_tools` accepts `mcp_toolset` with only `mcp_server_name` required | `configs` / `default_config` not validated |
| `src/fairy/views.py` | 501–523 | `_validate_mcp_servers` — url/stdio types, 20-max, name-unique | No header-value validation |
| `src/fairy/views.py` | 595–613 | `_serialize_agent` returns full `mcp_servers` incl. headers | **Security gap** |
| `src/fairy/views.py` | 378–395 | `send_prompt` re-passes `mcp_servers` on continue | Multi-turn persistence works |
| `src/fairy/sprites_exec.py` | 120–140 | `_build_mcp_claude` → `/tmp/mcp.json` | Headers verbatim |
| `src/fairy/sprites_exec.py` | 143–167 | `_codex_top_level_keys` — tool enforcement only | Never reads `mcp_toolset` |
| `src/fairy/sprites_exec.py` | 170–210 | `_build_mcp_codex` — url + bearer only | **No `enabled_tools` / `disabled_tools`** |
| `src/fairy/sprites_exec.py` | 213–233 | `_build_mcp_gemini` → `~/.gemini/settings.json` | **No `includeTools` / `excludeTools`** |
| `src/fairy/sprites_exec.py` | 258–264 | `_mcp_cmd_flags` — claude `--mcp-config --strict-mcp-config` | |
| `src/fairy/sprites_exec.py` | 286–321 | `_tool_flags_claude` — reads `mcp_toolset` in allowlist path only | **Denylist path ignores it** |
| `src/fairy/sprites_exec.py` | 342–392 | `_tool_files_gemini` — Policy Engine writer | Template for a new `_tool_files_claude_permissions` |
| `tests/e2e/test_agents.py` | 241–355 | `TestAgentTools` — CRUD validation for tools + mcp_servers | Pattern for validation-only tests |
| `tests/e2e/test_agent_tools.py` | 61–113 | `_parse_claude_tool_names` / `_parse_codex_tool_names` / `_parse_gemini_tool_names` | Extend for `mcp__*` filter |
| `tests/e2e/test_agent_tools.py` | 89–92 | Codex `mcp_tool_call` item parsing | Reuse verbatim |
| `tests/e2e/test_agent_tools.py` | 144–177 | `TestToolEnforcement` matrix fixture | Mirror for `TestMcpEnforcement` |
| `tests/e2e/conftest.py` | 20–27 | `RUNTIME_MODELS` pinned-cheap-model map | Use as-is |

## Architecture Insights

- **The `_tool_files_<runtime>` pattern introduced in `4c3b9cc` is extensible.** It produces `(cli_flags, files_to_write)` for each runtime. Adding MCP-specific deny wiring means either (a) adding a `_mcp_files_<runtime>` function with the same contract, or (b) extending `_tool_files_<runtime>` to also read `mcp_toolset` entries. Option (b) keeps everything tools-related in one function per runtime and matches the existing contract of `_build_tool_flags` which already takes `mcp_server_names`.

- **Codex is uniquely suited to per-tool MCP deny** — it's the only runtime with a first-class per-tool config key (`disabled_tools`). Claude needs a settings.json writer; Gemini needs a Policy Engine writer. Codex just needs extra keys in the same `config.toml` block it already emits.

- **Stream-event parsing is cheap to extend to MCP.** Codex parsing was already MCP-aware at [`test_agent_tools.py:89-92`](../../tests/e2e/test_agent_tools.py). Claude and Gemini just need a `name.startswith("mcp__")` filter on the same event shapes the existing tool-enforcement tests use.

- **Multi-turn MCP enforcement is free.** Fairy rebuilds the wrapper script on every turn (`continue_session=True` path re-calls `_build_mcp_section` and `_tool_flags_*`). Claude's continue-path non-persistence bug (from the tool-enforcement research) is also a non-issue here because Fairy re-emits everything.

## Historical Context

Relevant prior research in `thoughts/research/`:

- [`2026-04-16-agent-tool-enforcement-e2e.md`](./2026-04-16-agent-tool-enforcement-e2e.md) — the sibling research doc that led to `4c3b9cc`. The `_tool_files_*` pattern, stream-event shapes, and matrix structure are all directly transferable.
- [`2026-04-16-agent-tools-and-mcp.md`](./2026-04-16-agent-tools-and-mcp.md) — original research that shaped `Agent.tools` + `mcp_servers` fields. Documents the Managed Agents API mapping.
- [`2026-04-15-sprites-platform-research.md`](./2026-04-15-sprites-platform-research.md) and [`2026-04-16-sprites-deep-dive.md`](./2026-04-16-sprites-deep-dive.md) — confirm Sprites has unrestricted outbound network (key for the Fairy-hosted `/test-mcp` decision).

Relevant plans:
- [`thoughts/plans/2026-04-16-agent-tool-enforcement.md`](../plans/2026-04-16-agent-tool-enforcement.md) — the plan that shipped in `4c3b9cc`. Its "What We're NOT Doing" section explicitly scopes MCP enforcement out; this research picks up that thread.

## Open Questions

- **Should the e2e suite land as a bundle with Fairy wiring changes, or incrementally?** Recommendation: bundle. Allow-polarity and absence tests alone don't prove the high-value claim ("denying an MCP tool actually denies it"). An e2e file with 4 xfails + 1 passing test provides less signal than a coordinated wiring + e2e landing.

- **Claude settings.json writer: new file or extend `_tool_files_*`?** `_tool_files_claude` doesn't exist yet (claude uses CLI flags only today). Introducing it for MCP deny via `permissions.deny` would mirror gemini's `_tool_files_gemini`. Decide whether to also migrate built-in tool denial to settings.json or keep the current `--disallowedTools` path for built-ins.

- **Does Fairy want to expose `codex disabled_tools` / `gemini excludeTools` per-tool semantics in `mcp_toolset.configs`, or only server-level enable/disable?** Per-tool gives the richer API but requires validator changes to actually read `configs[].name` + `enabled`. Minimum viable wiring: server-level only (via `default_config.enabled`).

- **Is the Fairy-hosted `/test-mcp` endpoint dev-only, or should it persist in prod as a health-check?** Recommendation: dev-only (gate behind `DEBUG` or a `FAIRY_TESTING` env). A persistent test endpoint in prod is a small but real supply-chain vector if any tool does more than echo.

- **Security-hardening follow-up**: should `mcp_servers[].headers` encryption (and strip-from-response) land before or after this e2e work? These are orthogonal — this research doesn't block on it — but the plaintext exposure is worth a dedicated ticket.

## Related Research

- [`2026-04-16-agent-tool-enforcement-e2e.md`](./2026-04-16-agent-tool-enforcement-e2e.md) — direct sibling, identical structure for built-in tools
- [`2026-04-16-agent-tools-and-mcp.md`](./2026-04-16-agent-tools-and-mcp.md) — Agent model API design research
- [`2026-04-16-sprites-deep-dive.md`](./2026-04-16-sprites-deep-dive.md) — Sprites network reachability
