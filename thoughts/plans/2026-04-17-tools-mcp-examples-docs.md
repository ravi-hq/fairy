# Tools & MCP Example-Calls Docs Plan

## Overview

PRs #7 and #10 introduced two new surfaces on the agent resource — `Agent.tools`
(now actually enforced by the runtime) and richer `mcp_toolset` entries with
`configs` + `default_config` + cross-reference validation. Neither the README
nor any docs file shows a user how to *call* these APIs.

Goal: add a single doc file with copy-pasteable `curl` one-liners that exercise
every meaningful shape of `tools` and `mcp_servers` when **defining an agent**.
Scope is narrow — agent create/update only, not full end-to-end session flows.

## Research Summary

All research done in-repo (narrow single-domain scope, no team spawned).

### Key Discoveries

**Schema source of truth**: `src/fairy/views.py:464-509` is where tool and MCP
validation lives today. After PR #7 + #10:

- `VALID_TOOL_TYPES = {"agent_toolset_20260401", "mcp_toolset"}`
  (`custom` was removed in PR #7).
- `VALID_MCP_SERVER_TYPES = {"url", "stdio"}`.
- Max 20 MCP servers per agent (`src/fairy/views.py:508`).
- Cross-reference rule (PR #10, `src/fairy/views.py:_cross_validate_tool_refs`):
  every `mcp_toolset.mcp_server_name` must appear in `mcp_servers[].name`, or
  the request returns 422. Enforced on POST *and* PUT using the merged effective
  state.

**Built-in toolset (`agent_toolset_20260401`)** — see the capability table
PR #7 adds to `README.md`. Canonical tool names that can appear in
`configs[].name`: `bash`, `read`, `write`, `edit`, `glob`, `grep`,
`web_fetch`, `web_search`. Only enforced on claude/claude-oauth in PR #7;
gemini/codex plumbed but inactive until later stack PRs.

**`mcp_toolset` shape (PR #10)**:
```
{
  "type": "mcp_toolset",
  "mcp_server_name": "<must match an mcp_servers entry>",
  "default_config": {"enabled": true|false},     # server-wide default
  "configs": [{"name": "<tool>", "enabled": true|false}, ...]  # per-tool overrides
}
```
Normalized in `sprites_exec._build_mcp_toolset_rules` into
`McpToolsetRules(default_enabled, per_tool={...})`. Only wired into claude
today (via `~/.claude/settings.json` `permissions.allow/deny`).

**`mcp_servers` shape** (`src/fairy/sprites_exec.py:29-36`):
- URL form: `{type:"url", name, url, headers?: {...}}`
- stdio form: `{type:"stdio", name, command, args?:[...], env?:{...}}`

**Auth + host**: `Authorization: Bearer <FAIRY_API_TOKEN>` (from
`src/fairy/auth.py:20-26`). `make dev` serves on `:8777` — but e2e conftest
defaults to `:8000` when pytest is invoked directly. Doc should pick one.
Recommend `:8777` with a leading env-var line the reader can change.

**Response model** (`_serialize_agent` in `src/fairy/views.py:582+`): create
returns 201 with the full agent including `id`, `version: 1`, and the
normalized `tools`/`mcp_servers`. Update (PUT) requires a `version` field
for optimistic concurrency and returns the bumped version.

### Examples already in the codebase to mine from

- `tests/test_tools_mcp.py:171-229` — baseline create with `agent_toolset` +
  `mcp_toolset` referencing a URL MCP server (good "starter" shape).
- `tests/test_tools_mcp.py:411-439` — update path with `version:1`.
- `tests/e2e/test_agent_tools.py` (new in PR #7) — concrete deny-all and
  per-tool-deny shapes.
- `tests/e2e/test_mcp_enforcement.py` (new in PR #10) — `_mcp_toolset_deny_server`,
  `_mcp_toolset_allow_server`, and per-tool allow/deny builders.

## Current State Analysis

- No `docs/` directory exists in the repo. Must be created.
- README has an "API surface" section listing routes but no example payloads.
- No other example code (no `examples/`, no `scripts/`).

## Desired End State

A single new file at `docs/tools-and-mcp-examples.md` containing:

1. A short preamble defining the `$FAIRY_URL` / `$FAIRY_TOKEN` shell vars used
   by every example.
2. ~14 curl one-liners, each preceded by a one-sentence description of *what
   shape it demonstrates*, covering every meaningful combination of
   `tools` + `mcp_servers` on agent create, plus one update example.
3. A short "validation errors" subsection with 3–4 expected-failure curls that
   show the 422 responses users will hit.
4. A one-line pointer added to `README.md` under the API surface section, e.g.
   `See docs/tools-and-mcp-examples.md for curl examples of agents with tools
   and MCP servers.`

### Verification

- File exists at the target path; every curl is syntactically valid
  (`bash -n` on a temp script wrapping all commands).
- Every curl payload passes Pydantic validation when manually run against a
  local `make dev` server with a valid token — none should 422 except the
  explicitly-titled "validation errors" subsection.
- README diff is a single-line addition.

## What We're NOT Doing

- No full end-to-end flows (no environment create, no session create, no SSE
  streaming). Agent define only, per user.
- No Python / httpie / SDK alternatives — curl one-liners only.
- No screenshots, no response-body examples beyond what's needed to explain
  the `version` field on update.
- No per-runtime nuance discussion — the PR-added README capability tables
  already cover that, and this doc links to them rather than duplicating.
- No docs framework scaffolding (no mkdocs, no sphinx). Plain Markdown only.
- No `examples/` directory of runnable scripts — deferred; user said "docs
  folder seems fine for now."

## Implementation Approach

One-shot doc addition — no code, no tests, no migrations. Write the file,
add the README pointer, eyeball every curl against a live dev server to
catch typos. Single PR.

## File Ownership Map

| File | Change Type | Notes |
|------|-------------|-------|
| `docs/tools-and-mcp-examples.md` | create | The examples file |
| `README.md` | modify | Add single-line pointer under API surface |

No cross-track dependencies — sequential single-author implementation.

## Phase 1: Write the examples file

### Overview

Create `docs/tools-and-mcp-examples.md` with the structure below.

### File outline

```markdown
# Agents with tools and MCP servers — curl examples

Set these once per shell:

    export FAIRY_URL=http://localhost:8777
    export FAIRY_TOKEN=<your-api-token>

All examples target `POST /agents` (create) except the last, which shows a
`PUT /agents/{id}` update. See `src/fairy/urls.py` for the full route table
and `README.md` for the enforcement-capability tables per runtime.

## Built-in toolset (agent_toolset_20260401)

### 1. Allow all built-in tools (explicit, equivalent to omitting `tools`)
<curl>

### 2. Deny all built-in tools (sandboxed read-only-ish agent)
<curl with default_config.enabled=false and empty configs>

### 3. Allowlist: only bash + read + grep
<curl with default_config.enabled=false and configs enabling three tools>

### 4. Denylist: everything except web_fetch and web_search
<curl with default_config.enabled=true and configs disabling code tools>

## MCP servers

### 5. URL MCP server (GitHub public endpoint)
<curl with a single mcp_servers url entry, no tools>

### 6. URL MCP server with auth headers
<curl with headers: {"Authorization": "Bearer ${GITHUB_TOKEN}"}>

### 7. stdio MCP server (local npx binary)
<curl with type:"stdio", command:"npx", args, env>

### 8. Multiple MCP servers
<curl with two mcp_servers entries>

## MCP toolset restrictions

### 9. Allow a server (explicit, equivalent to declaring the server without mcp_toolset)
<curl with mcp_toolset, default_config.enabled=true>

### 10. Deny a whole server (server declared but all its tools blocked)
<curl with mcp_toolset default_config.enabled=false, no configs>

### 11. Per-tool denylist on a server
<curl with default_config.enabled=true + configs entries disabling dangerous tools>

### 12. Per-tool allowlist on a server (deny by default, allow specific tools)
<curl with default_config.enabled=false + configs entries enabling a few>

## Combining built-in + MCP toolsets

### 13. Restricted built-ins + MCP server + per-tool MCP allowlist
<curl combining everything so a reader sees the complete shape>

## Updating tools on an existing agent

### 14. PUT /agents/{id} with a version
<curl showing the optimistic-concurrency `version: N` requirement>

## Validation errors

Each of these returns `422 Unprocessable Entity`:

### A. Unknown tool type
<curl with {"type": "whatever"}>

### B. mcp_toolset missing mcp_server_name
<curl>

### C. mcp_toolset referencing a server not in mcp_servers
<curl — this is the PR #10 cross-validation rule>

### D. More than 20 MCP servers
<curl, briefly noted>
```

### Payload shapes to use

Concrete JSON bodies the curls should carry — each is mirrored from either a
test fixture or the PR's e2e suite so we know it passes validation today:

1. **Allow all**: `tools: [{"type": "agent_toolset_20260401"}]` (baseline,
   mirrors `tests/test_tools_mcp.py:179`).
2. **Deny all**:
   `tools: [{"type": "agent_toolset_20260401", "default_config": {"enabled": false}}]`.
3. **Allowlist 3**:
   `{"type": "agent_toolset_20260401", "default_config": {"enabled": false},
     "configs": [{"name":"bash","enabled":true},{"name":"read","enabled":true},
     {"name":"grep","enabled":true}]}`.
4. **Denylist code**: `default_config.enabled=true` + configs disabling
   `bash, edit, write, glob, grep, read`.
5. **URL server**:
   `mcp_servers: [{"type":"url","name":"github","url":"https://mcp.github.com/mcp"}]`
   (matches `tests/test_tools_mcp.py:186-191`).
6. **Headers**: add
   `"headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}`. Note in text that
   Fairy substitutes `${...}` env refs at runtime via the environment's
   `env_vars`, and point at `src/fairy/crypto.py` for background.
7. **stdio**:
   `{"type":"stdio","name":"fs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/workspace"]}`.
8. **Two servers**: one url + one stdio from (5) and (7).
9. **Allow server** (no-op mcp_toolset):
   `mcp_servers:[...]`, `tools:[{"type":"mcp_toolset","mcp_server_name":"github"}]`.
10. **Deny whole server**:
    add `"default_config": {"enabled": false}` to the mcp_toolset entry
    (matches `_mcp_toolset_deny_server` in `tests/e2e/test_mcp_enforcement.py`).
11. **Per-tool denylist**:
    mcp_toolset with `configs: [{"name":"dangerous_tool","enabled":false}]`.
12. **Per-tool allowlist**:
    mcp_toolset with `default_config.enabled=false` and
    `configs: [{"name":"signal_tool","enabled":true}]`.
13. **Combined**: example 3 + example 5 + example 11 inline — the single
    most useful template for a real agent.
14. **Update**:
    `PUT $FAIRY_URL/agents/$AGENT_ID` with body `{"version": 1, "tools": [...]}`.
    Call out that response returns `"version": 2` and that re-sending with
    `version:1` now 409s.

### Invalid payloads for the error section

- **A**: `tools:[{"type":"custom","name":"x","description":"y","input_schema":{}}]`
  — was valid before PR #7, now 422. Explicitly call this out since any
  existing callers will hit it.
- **B**: `tools:[{"type":"mcp_toolset"}]` — missing `mcp_server_name`.
- **C**: `tools:[{"type":"mcp_toolset","mcp_server_name":"ghost"}]` with
  `mcp_servers:[]` or a differently-named server — the PR #10
  cross-validation.
- **D**: just note the 20-server cap with a one-line comment rather than a
  full curl.

### Curl template to use consistently

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

One line per example, pretty-print the JSON body inside `-d '…'` with
readable whitespace — the shell accepts multi-line single-quoted strings, so
the "one-liner" constraint is really "one curl invocation per example," not
one physical line.

### Success Criteria

#### Automated Verification
- [ ] `ls docs/tools-and-mcp-examples.md` exists.
- [ ] File contains exactly the 14 agent-create/update examples + 3 error
      examples listed above (one section header per example).
- [ ] `make lint` still passes (no Python changes, should be a no-op but
      confirms we didn't accidentally edit source).
- [ ] `make test` still passes (ditto).

#### Manual Verification
- [ ] With `make dev` running and a valid `$FAIRY_TOKEN`, run each of the 14
      valid curls and confirm 201 (or 200 for #14). Delete each created agent
      after, or archive via `POST /agents/{id}/archive`.
- [ ] Run each of the 3 error curls (A/B/C) and confirm 422 with the expected
      `detail` message.
- [ ] README's new pointer line renders correctly on GitHub preview.

**Gate**: Human review that the examples are pedagogically ordered
(simple → complex) and that every non-obvious payload has a one-sentence
explanation before the curl.

---

## Phase 2: README pointer

### Overview

Add one line to `README.md` immediately after the API-surface bullet list:

```markdown
See [`docs/tools-and-mcp-examples.md`](docs/tools-and-mcp-examples.md) for
curl examples of agents with tools and MCP servers configured.
```

### Success Criteria

#### Automated Verification
- [ ] `grep -q "docs/tools-and-mcp-examples.md" README.md` succeeds.

#### Manual Verification
- [ ] Link resolves on GitHub PR preview.

---

## Testing Strategy

### Automated

None beyond the existing suite passing. This is a docs-only change.

### Manual

One pass of every curl against a local `make dev` server is the primary
verification. Prefer to run it after PR #10 is merged to `main` so the
cross-validation and new `mcp_toolset.configs` shape are live; otherwise
test against a branch that contains both PRs (e.g., a local merge or
fast-forward of `main + #7 + #10`).

## Performance Considerations

None.

## References

- PR #7 (Agent.tools enforcement): `thoughts/plans/2026-04-16-agent-tool-enforcement.md`
- PR #10 (mcp_toolset enforcement): `thoughts/plans/2026-04-17-agent-mcp-enforcement.md`
- Validation source: `src/fairy/views.py:464-509` (tool + MCP server validators),
  `src/fairy/views.py:_cross_validate_tool_refs` (cross-ref rule added in #10)
- Shape fixtures to mirror: `tests/test_tools_mcp.py:171-229`,
  `tests/e2e/test_agent_tools.py`, `tests/e2e/test_mcp_enforcement.py`
- Auth + base URL: `src/fairy/auth.py:11-26`, `CLAUDE.md` (dev server on :8777)
