# Skills Support (Phase 1) — Implementation Plan

## Overview

Make `Agent.skills` functional. Today the field is an unvalidated `JSONField` that is snapshotted in version history and returned in API responses but **never materialized onto a Sprite**. This phase teaches Fairy to (a) validate the skills shape on create/update and (b) write SKILL.md files onto the Sprite filesystem at the runtime-specific path before the agent CLI is `exec`'d, so all three runtimes (Claude Code, Codex, Gemini) discover and auto-invoke skills via their native progressive-disclosure mechanism.

No DB migration. No new endpoints. Shape change is additive to the existing `JSONField`.

## Research Summary

Backed by `thoughts/research/2026-04-17-agent-skills-support.md`.

- **Track 1 — Fairy internals**: `Agent.skills` is a `JSONField(default=list)` on both `Agent` and `AgentVersion`, fully plumbed through serialization/versioning but never consumed in `sprites_exec.py`. The `tools`/`mcp_servers` pair is the closest existing parallel.
- **Track 2 — Anthropic Skills API**: Claude Code CLI does **not** resolve remote `skill_id`s. Filesystem-only. Managed-agents `container.skills` is a separate surface that only works from `POST /v1/messages`.
- **Track 3 — Runtime CLIs**: All three runtimes implement the [agentskills.io](https://agentskills.io) standard — same SKILL.md format, different user-level paths: `~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/skills/`. Codex/Gemini also support repo-scoped `.agents/skills/`, but Fairy writes to the user-level path since Sprites are agent-home environments, not repo roots.
- **Track 4 — OSS ecosystem**: 36.82% of public skills carry ≥1 security flaw. Argues for inline content in Phase 1 rather than URL fetch.

### Key Discoveries

- `Agent.skills` declaration: `src/fairy/models.py:221`; `AgentVersion.skills` mirror: `src/fairy/models.py:252`.
- Validator pattern to copy: `_validate_mcp_servers` at `src/fairy/views.py:488-510` (per-item dict check, type allow-list, required per-type fields, dedup by name, cap at 20).
- Request schema attach points: `CreateAgentRequest.skills` at `src/fairy/views.py:520`, `UpdateAgentRequest.skills` at `src/fairy/views.py:553`.
- Wrapper-script dispatch pattern to copy: `_build_mcp_section(runtime_name, servers)` at `src/fairy/sprites_exec.py:196-206` with per-runtime writers.
- `build_wrapper_script` signature to extend: `src/fairy/sprites_exec.py:218-272`. The new section slots between `{mcp_section}` and the `exec` line (`src/fairy/sprites_exec.py:269-271`).
- Session entry point where skills must be passed: `create_session` calls `build_wrapper_script` at `src/fairy/views.py:225-228`; the continue path in `send_prompt` also rebuilds the wrapper at `src/fairy/views.py:378`.
- Test templates: `tests/test_tools_mcp.py:59-165` for wrapper-script coverage; `tests/test_tools_mcp.py:278-392` for validator coverage.
- SKILL.md spec: frontmatter requires `name` (`[a-z0-9-]+`, max 64 chars, reserved words "anthropic"/"claude") and `description` (max 1024 chars).

## Current State Analysis

- `Agent.skills` accepts any JSON (no `field_validator` in `CreateAgentRequest` or `UpdateAgentRequest`).
- Existing test data uses `[{"type": "web_search"}]` and `[{"type": "code_search"}]` (`tests/test_agents.py:45,69`) — a shape that doesn't map to anything in `sprites_exec.py`. These need to be updated to the new shape in this plan.
- `build_wrapper_script` takes `repos`, `environment`, `mcp_servers` but no `skills`.
- `/run-agent.sh` is written on the Sprite filesystem with sections in this order: env exports → cwd+git init → packages → clone → setup → MCP config → `exec`. Skills will be a new section immediately before `exec`.
- Continue-session path in `send_prompt` (`src/fairy/views.py:378`) rebuilds the wrapper without any MCP or env info. Today that is intentional (the Sprite already has them from session start) — but the wrapper is re-written, so the skills section must be repeated there too or the original `.claude/skills/` / `.agents/skills/` / `.gemini/skills/` trees from session start remain on disk and will still be discovered. **Decision**: re-materialize on continue for defensive symmetry; cheap and idempotent.

## Desired End State

1. `POST /agents` and `PUT /agents/{id}` validate the `skills` field. Invalid shapes return `422` with a specific error.
2. `POST /sessions` materializes the agent's skills onto the Sprite at the runtime-appropriate path before `exec`.
3. `POST /sessions/{id}/prompt` (continue) re-materializes skills on the same Sprite.
4. Skills work end-to-end on `claude`, `claude-oauth`, `codex`, and `gemini` runtimes with the same `Agent.skills` JSON input.
5. Existing tests pass; new tests cover validator edges, per-runtime paths, and wrapper ordering.

### Verification

- `make lint && make fmt` clean.
- `make test` passes (includes new validator + wrapper tests).
- Manual (local) `make dev` + `curl` flow: create an agent with one skill, launch a session on each runtime, verify the SKILL.md file appears in the expected path inside the Sprite via a `sprite.command("cat", ...)` check.
- Optional `FAIRY_API_TOKEN=... make test-e2e-fast` still passes.

## What We're NOT Doing

- **No new `Skill` / `SkillVersion` models.** That's Phase 2.
- **No new CRUD endpoints (`/skills`).** Phase 2.
- **No `supporting_files` / `scripts/` in SKILL.md.** Validator rejects those keys for now.
- **No `{source: "git+https://..."}` URL fetch.** Phase 3, gated on security review.
- **No `{type: "anthropic", skill_id: "xlsx"}` pass-through.** The CLI doesn't consume it; not viable.
- **No Anthropic Skills API calls from Fairy.** Fairy is a storage-and-injection layer only.
- **No per-runtime skill filtering.** All configured skills go to all runtimes via the appropriate path.

## Implementation Approach

Single phase, three sequential code areas, one test file. Mirrors the existing `mcp_servers` pattern end-to-end.

1. Add `_validate_skills` in `views.py`, wire into both request schemas.
2. Add `SkillSpec` dataclass, `_build_skills_section` dispatcher, and a `skills` parameter on `build_wrapper_script` in `sprites_exec.py`.
3. Extract `agent.skills` in both `create_session` and `send_prompt`, pass to `build_wrapper_script`.
4. Tests: validator edges, per-runtime wrapper paths, multi-skill ordering, continue-session behavior.

## File Ownership Map

| File | Change Type | Notes |
|------|-------------|-------|
| `src/fairy/sprites_exec.py` | modify | Add `SkillSpec`, `_build_skills_section`, `_build_skills_*` per-runtime helpers, new `skills` kwarg on `build_wrapper_script`. |
| `src/fairy/views.py` | modify | Add `_validate_skills`, attach to `CreateAgentRequest.skills` and `UpdateAgentRequest.skills`, add `MAX_SKILLS_PER_AGENT = 20`, convert `agent.skills` → `list[SkillSpec]` in `create_session` and `send_prompt`, pass to `build_wrapper_script`. |
| `tests/test_skills.py` | create | New module: wrapper-script mechanics + validator edges, modeled on `tests/test_tools_mcp.py`. |
| `tests/test_agents.py` | modify | Update the three existing `skills=[{"type": "web_search"}]` / `"code_search"` fixtures to the new `{name, description, content}` shape. |
| `tests/e2e/test_agents.py` | modify | Update one skills fixture for the new shape. |

Phase 2 concerns (`Skill` model, `/skills` endpoints) are deliberately out of this plan.

## Phase 1: Materialize skills onto the Sprite filesystem

### Overview

One logical phase. Three sequential steps because the code areas depend on each other (can't pass `skills=` to `build_wrapper_script` until the parameter exists; can't call `_build_skills_section` until it's defined).

### Changes Required

#### 1. `src/fairy/sprites_exec.py` — skill spec + wrapper integration

Add a `SkillSpec` dataclass near `McpServerSpec` at `src/fairy/sprites_exec.py:28`:

```python
@dataclass(frozen=True)
class SkillSpec:
    """A SKILL.md file to materialize on the Sprite filesystem.

    `content` is the full SKILL.md text including YAML frontmatter.
    `name` is the directory slug — must be a safe filename fragment.
    """
    name: str
    content: str
```

Add a per-runtime path resolver and a section builder. Insert below the existing MCP helpers (after `src/fairy/sprites_exec.py:215`):

```python
_SKILLS_ROOTS: dict[str, str] = {
    "claude": "/home/sprite/.claude/skills",
    "claude-oauth": "/home/sprite/.claude/skills",
    "codex": "/home/sprite/.codex/skills",
    "gemini": "/home/sprite/.gemini/skills",
}


def _build_skills_section(runtime_name: str, skills: list[SkillSpec]) -> str:
    """Emit shell commands that write each SKILL.md to the runtime's skills dir."""
    if not skills:
        return ""
    root = _SKILLS_ROOTS.get(runtime_name)
    if root is None:
        return ""
    lines = ["# Agent skills"]
    for s in skills:
        dir_path = f"{root}/{s.name}"
        lines.append(f"mkdir -p {shlex.quote(dir_path)}")
        lines.append(f"cat > {shlex.quote(dir_path + '/SKILL.md')} << 'SKILL_EOF'")
        lines.append(s.content)
        lines.append("SKILL_EOF")
    return "\n".join(lines)
```

Extend `build_wrapper_script` (`src/fairy/sprites_exec.py:218-272`) to accept `skills`:

```python
def build_wrapper_script(
    config: RuntimeConfig,
    api_key: str,
    prompt: str,
    *,
    continue_session: bool = False,
    repos: list[RepoSpec] | None = None,
    environment: EnvironmentSetup | None = None,
    mcp_servers: list[McpServerSpec] | None = None,
    skills: list[SkillSpec] | None = None,
) -> str:
    ...
    mcp_section = _build_mcp_section(config.name, mcp_servers or [])
    mcp_flags = _mcp_cmd_flags(config.name, mcp_servers or [])
    skills_section = _build_skills_section(config.name, skills or [])

    return f"""#!/bin/bash
set -euo pipefail
export {config.env_var}={shlex.quote(api_key)}
export PROMPT={shlex.quote(prompt)}
{env_vars_section}

# Setup working directory
cd /home/sprite
mkdir -p .gemini
if [ ! -d .git ]; then
    git init -q
    git add -A 2>/dev/null || true
    git commit -q -m "init" --allow-empty 2>/dev/null || true
fi

{packages_section}

{clone_section}

{setup_section}

{mcp_section}

{skills_section}

exec {cmd}{mcp_flags}
"""
```

The heredoc strategy (`<< 'SKILL_EOF'`) with single-quoted delimiter means SKILL.md content is written verbatim — no variable expansion, no escaping needed. The validator (below) blocks the literal string `SKILL_EOF` in content to prevent heredoc termination injection.

#### 2. `src/fairy/views.py` — validator + session wiring

Add constants and validator near the existing `_validate_mcp_servers` at `src/fairy/views.py:488`:

```python
MAX_SKILLS_PER_AGENT = 20
MAX_SKILL_NAME_LEN = 64
MAX_SKILL_DESCRIPTION_LEN = 1024
MAX_SKILL_CONTENT_BYTES = 64 * 1024  # 64 KB

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SKILL_NAME_RESERVED = {"anthropic", "claude"}
_SKILL_ALLOWED_KEYS = {"name", "description", "content"}


def _validate_skills(skills: list) -> list:
    if len(skills) > MAX_SKILLS_PER_AGENT:
        raise ValueError(f"Maximum {MAX_SKILLS_PER_AGENT} skills per agent")
    seen_names: set[str] = set()
    for i, skill in enumerate(skills):
        if not isinstance(skill, dict):
            raise ValueError(f"skills[{i}] must be an object")

        extra = set(skill) - _SKILL_ALLOWED_KEYS
        if extra:
            raise ValueError(
                f"skills[{i}]: unknown keys {sorted(extra)!r}. "
                f"Allowed: {sorted(_SKILL_ALLOWED_KEYS)}"
            )

        for field_name in ("name", "description", "content"):
            if field_name not in skill:
                raise ValueError(f"skills[{i}] missing required field: {field_name}")
            if not isinstance(skill[field_name], str):
                raise ValueError(f"skills[{i}].{field_name} must be a string")

        name = skill["name"]
        if not _SKILL_NAME_RE.match(name):
            raise ValueError(
                f"skills[{i}].name {name!r} must match [a-z0-9][a-z0-9-]{{0,63}}"
            )
        if name in _SKILL_NAME_RESERVED:
            raise ValueError(f"skills[{i}].name {name!r} is reserved")
        if name in seen_names:
            raise ValueError(f"skills[{i}]: duplicate name {name!r}")
        seen_names.add(name)

        if len(skill["description"]) > MAX_SKILL_DESCRIPTION_LEN:
            raise ValueError(
                f"skills[{i}].description exceeds {MAX_SKILL_DESCRIPTION_LEN} chars"
            )

        content = skill["content"]
        if len(content.encode("utf-8")) > MAX_SKILL_CONTENT_BYTES:
            raise ValueError(
                f"skills[{i}].content exceeds {MAX_SKILL_CONTENT_BYTES} bytes"
            )
        if "SKILL_EOF" in content:
            raise ValueError(
                f"skills[{i}].content must not contain the substring 'SKILL_EOF'"
            )
    return skills
```

Attach to the request schemas at `src/fairy/views.py:520` and `src/fairy/views.py:553`:

```python
class CreateAgentRequest(BaseModel):
    ...
    skills: list = Field(default_factory=list)
    ...

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, v: list) -> list:
        return _validate_skills(v)


class UpdateAgentRequest(BaseModel):
    ...
    skills: list | None = None
    ...

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, v: list | None) -> list | None:
        if v is not None:
            _validate_skills(v)
        return v
```

Add a helper next to `_mcp_servers_to_specs` at `src/fairy/views.py:44`:

```python
def _skills_to_specs(skills: list[dict]) -> list[SkillSpec]:
    return [SkillSpec(name=s["name"], content=s["content"]) for s in skills]
```

Import the new symbol: in the existing `from fairy.sprites_exec import ...` line (`src/fairy/views.py:22`), add `SkillSpec`.

Wire it into `create_session` (`src/fairy/views.py:224-228`):

```python
mcp_specs = _mcp_servers_to_specs(agent_obj.mcp_servers) if agent_obj else []
skill_specs = _skills_to_specs(agent_obj.skills) if agent_obj else []
script = build_wrapper_script(
    config, api_key, effective_prompt,
    repos=repo_specs, environment=env_setup,
    mcp_servers=mcp_specs, skills=skill_specs,
)
```

And into `send_prompt` (`src/fairy/views.py:378`):

```python
skill_specs = _skills_to_specs(session.agent.skills) if session.agent else []
script = build_wrapper_script(
    config, api_key, req.prompt,
    continue_session=True, skills=skill_specs,
)
```

Note: `send_prompt` currently passes no `environment` or `mcp_servers` (those are pre-installed on the Sprite from session start). Skills follow the same logic for symmetry — re-materializing is idempotent and defends against a user archiving/unarchiving an agent mid-session.

#### 3. `tests/test_skills.py` — new test module

Model on `tests/test_tools_mcp.py:59-392`. Cover:

**Wrapper-script mechanics:**
- `test_claude_skills_written_to_dot_claude_skills` — `_build_skills_section("claude", [SkillSpec("foo", "---\nname: foo\n---\nbody")])` emits `mkdir -p /home/sprite/.claude/skills/foo` and `cat > /home/sprite/.claude/skills/foo/SKILL.md`.
- `test_codex_skills_written_to_codex_dir` — path is `/home/sprite/.codex/skills/foo/SKILL.md`.
- `test_gemini_skills_written_to_gemini_dir` — path is `/home/sprite/.gemini/skills/foo/SKILL.md`.
- `test_claude_oauth_shares_claude_path` — `claude-oauth` uses `/home/sprite/.claude/skills/foo`.
- `test_no_skills_backward_compat` — empty list produces no skills section (parallel to `test_no_mcp_backward_compat` at `tests/test_tools_mcp.py:138`).
- `test_multiple_skills_separate_dirs` — two skills produce two `mkdir -p` commands with distinct paths.
- `test_skills_section_before_exec` — skills section string appears before `exec` in the script and after the MCP section (parallel to `test_mcp_section_before_exec` at `tests/test_tools_mcp.py:154`).
- `test_skill_content_verbatim` — content with shell metacharacters (`$VAR`, backticks, `$(cmd)`) is emitted verbatim inside the single-quoted heredoc.

**Validator edges (via Django test client):**
- `test_skill_name_slug_validation` — reject `Name`, `name with space`, `-leading-dash`, `name!`, name longer than 64 chars.
- `test_skill_name_reserved` — reject `anthropic`, `claude`.
- `test_skill_name_duplicate` — reject two skills with the same name.
- `test_skill_required_fields` — reject missing `name`, `description`, `content`.
- `test_skill_unknown_keys_rejected` — send `{"name": "x", "description": "y", "content": "z", "scripts": {"a": "b"}}` → 422.
- `test_skill_description_max_length` — 1025 chars rejected, 1024 accepted.
- `test_skill_content_max_size` — content > 64KB rejected.
- `test_skill_content_eof_injection` — content containing `SKILL_EOF` rejected.
- `test_skills_max_20` — 21 skills → 422 (parallel to `test_mcp_server_max_20` at `tests/test_tools_mcp.py:374`).
- `test_agent_skills_persisted_and_returned` — POST with valid skills → 200; GET returns same shape.
- `test_agent_skills_versioned_on_update` — PUT with skills change creates a new `AgentVersion`; `/versions` endpoint includes the new skills.
- `test_session_writes_skill_to_sprite_script` — using the existing session-creation test pattern (a stub `SpritesClient` or inspecting the generated script before `create_sprite` is called), verify the run script contains the expected `cat > .../SKILL.md` line.

#### 4. Fix-ups to existing tests

These tests currently use the old placeholder shape and will start failing once the validator is added:

- `tests/test_agents.py:45` — change `skills=[{"type": "web_search"}]` to e.g. `skills=[{"name": "web-search", "description": "Search the web", "content": "---\nname: web-search\ndescription: Search the web\n---\n"}]`.
- `tests/test_agents.py:69, 82, 103, 303` — update to the new shape (most use `"code_search"` — rename to `code-search`).
- `tests/e2e/test_agents.py:23, 32, 44` — same.

A one-time `skills_fixture()` helper in `tests/conftest.py` returning a canonical valid skill would keep these DRY.

### Success Criteria

#### Automated

- [ ] `make lint` passes.
- [ ] `make fmt` produces no diff.
- [ ] `make test` passes (new `test_skills.py` plus updated `test_agents.py`).
- [ ] No new migrations in `src/fairy/migrations/` (confirm with `ls`; shape change is JSON-only).

#### Manual

- [ ] With `make dev` running, `curl -X POST /agents` with the new skills shape returns 201 and echoes the skills back.
- [ ] `curl -X POST /agents` with `skills: [{"type": "web_search"}]` (old shape) returns 422 with a clear error.
- [ ] `curl -X POST /agents` with `skills: [{"name": "claude", ...}]` returns 422 (reserved name).
- [ ] `curl -X POST /agents` with 21 valid skills returns 422.
- [ ] Start a session on `claude` runtime; `sprite.command("ls", "/home/sprite/.claude/skills/<name>/").run()` shows `SKILL.md`; `cat` shows the content verbatim.
- [ ] Same verification on `codex` (`.codex/skills/...`) and `gemini` (`.gemini/skills/...`).
- [ ] `POST /sessions/{id}/prompt` on a completed session re-writes the SKILL.md (check `mtime` on the file in the Sprite).
- [ ] `FAIRY_API_TOKEN=<token> make test-e2e-fast` passes.

#### E2E smoke (optional, cost-gated)

- [ ] Run one `@slow` e2e test where the agent is asked to invoke the configured skill by name; assert the skill's declared behavior appears in the output stream.

**Gate**: Run all automated checks + a manual session on at least the `claude` runtime before merging. Full e2e suite (`make test-e2e`) optional but recommended before the PR lands since this touches session execution.

## Testing Strategy

### Automated

- Unit: `_validate_skills` edges (12+ cases).
- Unit: `_build_skills_section` per-runtime path output.
- Integration: `build_wrapper_script` with skills — section order, verbatim content, backward-compat when skills empty.
- Integration: Agent API accepts/rejects new shape; versioning continues to snapshot skills.

### Manual

- One agent-create per runtime via `make dev` + `curl`.
- SSH/exec into a running Sprite to confirm file tree matches runtime expectations.
- Sanity check that old agents (whose `skills` field may still hold `[{"type": "web_search"}]`) still return on `GET /agents/{id}` without 500. The validator runs on write only — old rows read fine. Confirm by creating an agent via the Django shell with the old shape, then `GET` it.

## Performance Considerations

- Per-session cost: one `mkdir -p` + one heredoc write per skill. Negligible vs existing session startup (package install + git clone dominate).
- Token cost at runtime: handled by the CLI's native progressive disclosure — only skill `name` + `description` always loaded (~100 tokens each); body loads on invocation.
- 20-skill cap × 64KB content = max 1.28MB of SKILL.md material written per session. Fits comfortably inside the Sprite filesystem.

## Security Considerations

- **Heredoc injection**: the literal string `SKILL_EOF` in skill content would terminate the heredoc early. Validator rejects it.
- **Path injection via name**: regex `^[a-z0-9][a-z0-9-]{0,63}$` prevents `..`, `/`, null bytes, or shell metacharacters reaching the `mkdir`/`cat` commands. Still shell-quote via `shlex.quote` for defense-in-depth.
- **Prompt injection via content**: in-scope risk — a user's skill content could attempt to subvert the agent. This is user-owned data (agent ↔ user FK at `src/fairy/models.py:211`) so the trust model is "the user is attacking themselves." Acceptable in Phase 1; Phase 2 can add per-skill signing or curation if needed.
- **No `scripts/` / `supporting_files` in Phase 1**: the validator's unknown-key rejection prevents silent acceptance of fields we haven't wired up yet. Moving to Phase 2 requires an explicit schema extension.
- **Sprite isolation**: multi-tenant risk is bounded — each session is a dedicated Sprite, so a malicious skill's worst case is owning its own container.

## References

- Research: `thoughts/research/2026-04-17-agent-skills-support.md`
- Closest parallel implementation (MCP servers): `src/fairy/views.py:488-510`, `src/fairy/sprites_exec.py:120-215`
- Test templates: `tests/test_tools_mcp.py:59-392`
- Agent Skills standard: https://agentskills.io
