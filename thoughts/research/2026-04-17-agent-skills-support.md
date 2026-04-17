---
date: 2026-04-17T10:52:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 9ea0965e5112cea2cee4fbb1ac8d18ad2b9eb833
branch: main
repository: ravi-hq/fairy
topic: "How should Fairy support Skills on its agents?"
tags: [research, team-research, agents, skills, runtimes, design]
status: complete
method: agent-team
team_size: 5
tracks: [fairy-internals, anthropic-api, runtime-cli, oss-ecosystem, synthesis]
last_updated: 2026-04-17
last_updated_by: Claude Code
---

# Research: How should Fairy support Skills on its agents?

**Date**: 2026-04-17T10:52:00-07:00
**Researcher**: Claude Code (team-research)
**Git Commit**: [`9ea0965`](https://github.com/ravi-hq/fairy/commit/9ea0965e5112cea2cee4fbb1ac8d18ad2b9eb833)
**Branch**: `main`
**Repository**: ravi-hq/fairy
**Method**: Agent team (5 specialist researchers running in parallel)

## Research Question

We need to figure out how we want to support skills within our agents. Claude
managed agents does it with some of their existing skills infrastructure. We
could choose to add a skills model to our database. We could hook up with
something open-source, we could pull from URLs. The seed prompt included the
Claude managed-agents Skills API docs (skill_id references, `anthropic` vs
`custom`, max 20 per session).

## Summary

Skills should be materialized as on-disk `SKILL.md` files inside the Sprite
before the runtime CLI is `exec`'d. The Claude Code CLI does **not** resolve
remote `skill_id`s from the Anthropic managed-agents API — those two systems
are fully separate. This eliminates the pass-through option.

The good news: all three runtimes Fairy supports (Claude Code, Codex, Gemini)
implement the same [Agent Skills open standard](https://agentskills.io) — an
identical SKILL.md format with progressive disclosure, differing only by the
on-disk path they scan. A single skills feature works cross-runtime.

`Agent.skills` already exists as an unvalidated `JSONField` (`models.py:221`)
but is never consumed — it is pure data today. The recommended wedge is to add
a field validator and teach `build_wrapper_script` to write the SKILL.md files
onto the Sprite filesystem before exec, mirroring how `mcp_servers` already
works. Phase 2 extracts skills to a first-class `Skill`/`SkillVersion` model
paralleling `Environment`.

## Research Tracks

### Track 1: Fairy internals & integration points
**Researcher**: fairy-researcher
**Scope**: `src/fairy/*.py`, migrations, tests — traced the life of the existing `skills` field.

#### Findings:
1. **`Agent.skills` is an unvalidated JSONField** — `default=list, blank=True` on both `Agent` and `AgentVersion`, accepts arbitrary JSON, no field_validator (`src/fairy/models.py:221`, `src/fairy/models.py:252`, `src/fairy/migrations/0005_add_agent_model.py:60`).
2. **The field is a pure data stub** — no references in `sprites_exec.py`; never materialized onto a Sprite; the field currently has no runtime effect (confirmed by full-file read of `src/fairy/sprites_exec.py`).
3. **Serialization plumbing exists** — accepted in `CreateAgentRequest.skills` and `UpdateAgentRequest.skills` (`src/fairy/views.py:520`, `src/fairy/views.py:553`), returned in `_serialize_agent` (`src/fairy/views.py:594`) and `_serialize_agent_version` (`src/fairy/views.py:613`), snapshotted in `_snapshot_version` (`src/fairy/views.py:624-637`), included in the update loop at `src/fairy/views.py:744`.
4. **`tools`/`mcp_servers` are the closest parallel pattern** — `_validate_tools` (`src/fairy/views.py:468-485`) and `_validate_mcp_servers` (`src/fairy/views.py:488-510`) validate each item is a dict, has a `type` in an allow-list, enforces required per-type fields, caps at 20. Skills has no equivalent.
5. **MCP→Sprite translation** — `_mcp_servers_to_specs(agent_obj.mcp_servers)` (`src/fairy/views.py:224`) builds `McpServerSpec` dataclasses; `_build_mcp_section(config.name, servers)` (`src/fairy/sprites_exec.py:196-206`) dispatches to runtime-specific writers that write `/tmp/mcp.json` (Claude), `~/.codex/config.toml` (Codex), `~/.gemini/settings.json` (Gemini). Skills should follow this exact pattern.
6. **Wrapper script structure** — `build_wrapper_script` (`src/fairy/sprites_exec.py:218-272`) writes `/run-agent.sh` with sections: env-var exports → packages → git clones → custom setup → MCP config → `exec {cmd}{flags}` at `src/fairy/sprites_exec.py:271`. The skills section slots naturally between MCP and `exec`.
7. **Optimistic-concurrency versioning** — Agent version bump requires `req.version == agent.version` or returns 409 (`src/fairy/views.py:719-723`); `_snapshot_version` writes an `AgentVersion` row after each change (`src/fairy/views.py:624-637`). A Phase 2 `Skill` model should mirror `Environment`/`EnvironmentVersion` (`src/fairy/models.py:141-205`).
8. **Git clone precedent** — `_build_clone_section` with token credential helper (`src/fairy/sprites_exec.py:86-117`) is already the reusable primitive if skills ever need remote fetch.
9. **Secrets pattern** — encryption is Fernet-based (`src/fairy/crypto.py`), applied only to `UserRuntimeKey.encrypted_key` and `SessionResource.encrypted_token`. Skills are code/docs, not secrets — no encryption needed unless a skill carries a private-repo token.

### Track 2: Anthropic managed-agents Skills API
**Researcher**: anthropic-api-researcher
**Scope**: Anthropic public docs, SDK surface, beta headers.

#### Findings:
1. **SKILL.md format** — required YAML frontmatter fields: `name` (max 64 chars, `[a-z0-9-]+`, no "anthropic"/"claude" reserved words) and `description` (max 1024 chars). Optional Claude Code fields: `when_to_use`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `model`, `effort`, `context`, etc. Skill directory can contain `SKILL.md`, `*.md` reference docs, and `scripts/` helpers. ([overview](https://platform.claude.com/docs/en/docs/agents-and-tools/agent-skills/overview), [best-practices](https://platform.claude.com/docs/en/docs/agents-and-tools/agent-skills/best-practices))
2. **Progressive disclosure** — ~100 tokens of metadata per skill always loaded; SKILL.md body (<5K tokens) loaded when triggered; supporting files loaded on-demand via bash — so the code inside `scripts/` never enters context, only its output.
3. **Lifecycle API** — `POST /v1/skills` (requires `anthropic-beta: skills-2025-10-02`) with full CRUD: create, list, get, delete, and per-version endpoints (`/v1/skills/{id}/versions`). Upload cap: 30 MB. Custom skills are **workspace-scoped**, not user-scoped. ([skills-guide](https://platform.claude.com/docs/en/build-with-claude/skills-guide))
4. **Attachment to Messages API** — via `container.skills: [{type, skill_id, version}]` on `POST /v1/messages`. Server-side resolution only — the client never uploads skill material at attach time. **Max 8 per request** (docs; seed prompt's "20" is incorrect).
5. **Pre-built Anthropic skills** — `pdf`, `docx`, `xlsx`, `pptx` available to all API users; `claude-api` also open-source on GitHub ([anthropics/skills](https://github.com/anthropics/skills)).
6. **Beta headers** — Skills API management: `skills-2025-10-02`. For Messages API use, three headers required: `code-execution-2025-08-25`, `skills-2025-10-02`, `files-api-2025-04-14`. **No `managed-agents-2026-04-01` header exists in public docs** — the seed prompt may have referenced a speculative/internal header.
7. **Critical finding — CLI does not resolve remote skill_ids** — "Custom Skills in Claude Code are filesystem-based and don't require API uploads." The Messages API skills system and the Claude Code CLI skills system are entirely separate. Skills do **not** sync across surfaces.
8. **Multi-tenant blockers** — workspace-scoped skills preclude per-user API uploads; cross-surface non-sync; no ZDR eligibility for skills data.

### Track 3: Runtime-level skill mechanics
**Researcher**: runtime-researcher
**Scope**: Claude Code CLI, Codex CLI, Gemini CLI skill discovery on disk.

#### Findings:
1. **All three CLIs implement the Agent Skills open standard** — originated by Anthropic, adopted by 30+ agent tools including the three Fairy runtimes. Identical SKILL.md format and discovery semantics across Claude Code, Codex, Gemini. ([agentskills.io](https://agentskills.io), [code.claude.com/skills](https://code.claude.com/docs/en/skills), [developers.openai.com/codex/skills](https://developers.openai.com/codex/skills))
2. **Per-runtime filesystem paths** —

   | Runtime | User path (what Fairy writes) | Repo path | Fallback |
   |---|---|---|---|
   | `claude` / `claude-oauth` | `/home/sprite/.claude/skills/<slug>/SKILL.md` | `.claude/skills/<slug>/SKILL.md` | `.claude/commands/<name>.md` legacy |
   | `codex` | `/home/sprite/.codex/skills/<slug>/SKILL.md` | `.agents/skills/<slug>/SKILL.md` (scanned CWD → repo root) | `AGENTS.md` for static context |
   | `gemini` | `/home/sprite/.gemini/skills/<slug>/SKILL.md` | `.gemini/skills/` or `.agents/skills/` | `GEMINI.md` for static context |

3. **User-level, not repo-level** — Fairy writes to each runtime's user-home path (`~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/skills/`) because Sprites are agent-home environments, not repo roots; the `.agents/skills/` alias that Codex/Gemini support is repo-scoped and wouldn't be picked up by a CLI invoked outside a cloned repo.
4. **Progressive-disclosure matches Anthropic spec** — each CLI scans skill directories at session start and injects only `name` + `description` metadata into the system prompt. Full body loads on explicit `/skill-name` invocation or model-initiated trigger. Pre-exec filesystem writes are sufficient — no runtime API calls needed.
5. **`claude-oauth` is the same binary as `claude`** — identical CLI invocation per `src/fairy/runtimes.py:56-80`, differing only by env var (`ANTHROPIC_API_KEY` vs `CLAUDE_CODE_OAUTH_TOKEN`). Skills path is identical.
6. **Fallback not needed** — since all runtimes have native skills support, concatenating skill bodies into the `system` prompt (the "dumb fallback" option) is not required. Native progressive disclosure is always preferable for token efficiency.

### Track 4: Open-source skill ecosystems & distribution
**Researcher**: oss-researcher
**Scope**: public skill repos, distribution mechanisms, trust/safety models.

#### Findings:
1. **Ecosystem inventory** — `travisvn/awesome-claude-skills` (~10.9k stars), `VoltAgent/awesome-agent-skills` (1000+ cross-platform), `sickn33/antigravity-awesome-skills` (~1,400), `anthropics/skills` (official spec + reference skills), `karanb192/awesome-claude-skills` (50+), `agentskills/agentskills` (spec + `skills-ref` CLI), `iflytek/skillhub` (self-hosted enterprise registry).
2. **Distribution hubs** — `skills.sh` (Vercel, Jan 2026, open directory + install telemetry + Snyk scanning); ClawHub (community marketplace); Claude Code plugin marketplace (`marketplace.json` protocol, git/npm/OCI sources, `strictKnownMarketplaces` for lock-down).
3. **Distribution model comparison** —

   | Model | Reproducibility | Versioning | Trust | Cold-start latency | Blast radius |
   |---|---|---|---|---|---|
   | Git clone (SHA-pinned) | High | Tags + SHA | Low (no signing) | Medium | Medium |
   | Git clone (floating branch) | Low | Mutable tags | Low | Medium | **High** — any push changes behavior |
   | npm registry | High with lockfile | SemVer + lockfile | Medium (Sigstore) | Low–medium | Medium–high (transitive deps) |
   | OCI artifact | Very high (digest) | Tags + digest | High (Cosign, SLSA, SBOM) | Low (layer-cache) | Low |
   | Tarball URL | Low | Manual | Low | Low | **Very high** |
   | Plugin marketplace | High when SHA-pinned | Per-source + plugin.json | Medium (Anthropic curated / community unreviewed) | Medium (cached) | Medium |
   | Hosted registry (SkillHub / skills.sh) | High | SemVer + tags | Medium–high (RBAC + audit) | Low | Medium |
   | Seed dir / pre-baked image | Very high | Build-time | High (admin-controlled) | **Zero** | Very low |

4. **Trust & safety landscape** — Snyk audit of 3,984 public skills (March 2026): **36.82% have ≥1 security flaw, 13.4% have ≥1 critical**, 280+ exposed API keys. ClawHavoc campaign deployed 1,184 malicious skills. OWASP published "Agentic Skills Top 10" in 2026. Top mitigations: SHA/digest pinning, Cosign signing, containerization as default, `allowed-tools` in SKILL.md, audit logs.
5. **Prior art for URL/git-fetch at session start** — Claude plugin marketplace SHA-pinned git source; sparse-clone for monorepos; `CLAUDE_CODE_PLUGIN_SEED_DIR` env var to pre-bake plugin cache at image build time (strongest cold-start solution); Arconia `skills.lock.json` with OCI layer caching.
6. **Fairy-specific synthesis** — best pragmatic path is SHA-pinned git for user/community skills + seed directory baked into Sprite images for Fairy-managed ones. OCI is the right long-term direction as ecosystem matures. Floating branch refs must be blocked.

### Track 5: Design trade-offs & recommendation
**Researcher**: synthesis-researcher
**Scope**: cross-cutting synthesis of tracks 1–4 into a concrete recommendation.

See **Architecture Insights** and **Recommendation** below — synthesis output is the backbone of this document.

## Cross-Track Discoveries

- **The managed-agents API is irrelevant to Fairy today.** Track 2 established that Claude Code CLI is filesystem-only for skill discovery; Track 3 independently confirmed the same for Codex and Gemini. The `{type: "anthropic", skill_id: "xlsx"}` pass-through in the seed prompt is not usable from a CLI-based runtime — it would require Fairy to switch to direct Messages API execution, which is a much bigger architectural change.
- **Cross-runtime skills are free.** Tracks 2 and 3 converge on agentskills.io as a common standard — the same SKILL.md content serves all three runtimes; only the directory path differs. This collapses "how do we make skills portable?" into a path-routing question.
- **The existing `Agent.skills` JSONField is a shovel-ready slot.** Track 1 showed the field is fully wired through serialization, versioning, and update — but unvalidated and unused. Adding validation + a call to a new `_build_skills_section` in the wrapper script closes the loop without any DB migration.
- **Security surface is script execution, not skill delivery.** Tracks 3 and 4 together show that the real risk is skills with `scripts/` running inside the Sprite — which Sprite isolation already contains. Delivery-channel risk (URL fetch at session start) can be deferred by starting with inline content.

## Architecture Insights

- **Patterns to mirror**: `Environment` / `EnvironmentVersion` (`src/fairy/models.py:141-205`) is the template for a future `Skill` / `SkillVersion`. `_validate_mcp_servers` + `_build_mcp_section` (`src/fairy/views.py:488`, `src/fairy/sprites_exec.py:196`) is the template for `_validate_skills` + `_build_skills_section`.
- **Where skills slot into the wrapper**: between the MCP config section and the `exec` call at `src/fairy/sprites_exec.py:269-271`. They need no CLI flags on any runtime — disk presence is sufficient.
- **No encryption needed**: skills are code/docs. The existing `crypto.py` pattern applies only if Phase 3 adds private-repo fetch.
- **Multi-turn implication**: the continue path (`POST /sessions/{id}/prompt`, `src/fairy/views.py:338-404`) also calls `build_wrapper_script` — skills will need to be materialized on re-entry too (or the prior Sprite filesystem must persist, which is already the case since the Sprite is the same).

## Recommendation

### TL;DR
Ship a small wedge (Option C below) in ~1 week: validate the existing `Agent.skills` JSON shape and teach `build_wrapper_script` to write SKILL.md files onto the Sprite at the runtime-specific path. Do not attempt managed-agents API pass-through — it does not work with CLI runtimes. Defer the first-class `Skill` model to Phase 2 after validating the feature with inline content.

### Options compared

| | A: API pass-through | B: Hosted `Skill` model | **C: Inline SKILL.md in `Agent.skills`** | D: Hybrid |
|---|---|---|---|---|
| **Viability** | **Dead end** — CLI doesn't resolve remote `skill_id`s | Viable; mirrors Environment | **Recommended starting wedge** | Collapses to B+C since A is unavailable |
| **DB impact** | None | New `Skill`+`SkillVersion` tables | None — shape change only | B+C |
| **API impact** | None | New CRUD surface | Field validator only | Full B surface |
| **Cross-runtime** | Claude-only at best | Full | Full | Full |
| **Cold-start** | N/A | Low — from DB | Same | Same |
| **Dev effort** | ~0, non-functional | 5–8 days | **2–3 days** | — |

### Phase 1 — smallest wedge (~1 week)

**Shape of `Agent.skills` entries:**
```json
[
  {
    "name": "my-skill",
    "description": "When to use and what it does",
    "content": "---\nname: my-skill\ndescription: ...\n---\n\nMarkdown body"
  }
]
```

**File changes:**
- `src/fairy/views.py`: add `_validate_skills(list) -> list` mirroring `_validate_tools` at `src/fairy/views.py:468`. Apply to `CreateAgentRequest.skills` (`src/fairy/views.py:520`) and `UpdateAgentRequest.skills` (`src/fairy/views.py:553`). In `create_session` (`src/fairy/views.py:224`), extract `agent_obj.skills` and pass as `skills=` to `build_wrapper_script`. Also call it in `send_prompt` so continued sessions re-materialize skills.
- `src/fairy/sprites_exec.py`: add a `SkillSpec` dataclass, a `_build_skills_section(runtime_name, skills)` function, and a `skills: list[SkillSpec] | None` parameter to `build_wrapper_script` (`src/fairy/sprites_exec.py:218`). Inject the new section between the MCP section and the `exec` line at `src/fairy/sprites_exec.py:269-271`.
- Tests: parallel to existing tools/mcp coverage — `test_agents.py` for validator edge cases, a new or expanded `test_sprites_exec.py` for correct per-runtime paths and ordering.

**No DB migration required** — `Agent.skills` remains a JSONField; only the accepted shape changes. Old unvalidated rows are tolerated on read.

**Per-runtime path dispatch:**
- `claude` / `claude-oauth` → `/home/sprite/.claude/skills/<slug>/SKILL.md`
- `codex` → `/home/sprite/.codex/skills/<slug>/SKILL.md`
- `gemini` → `/home/sprite/.gemini/skills/<slug>/SKILL.md`

### Phase 2 — first-class `Skill` model

Once usage is validated, extract skills content into `Skill` + `SkillVersion` tables paralleling `Environment` (`src/fairy/models.py:141-205`). Add `POST/GET/PUT/DELETE /skills` and archive/versions endpoints paralleling `/environments`. Evolve `Agent.skills` to accept `{"skill_id": "<uuid>"}` references that resolve to content at session start — Phase 1's `_build_skills_section` needs no changes because resolution happens in views before it is called. Add `Skill.supporting_files` (`{relative_path: content}`) for `scripts/helper.py` etc., with strict validation against `..` and absolute paths.

### Phase 3 — consider, do not commit

- URL/git-fetch skills (`{"source": "git+https://...", "ref": "<sha>"}`) leveraging the existing `_build_clone_section` precedent (`src/fairy/sprites_exec.py:86-117`). Block floating branch refs; require SHA pinning. Gate behind a user-level opt-in given the 36.82% vulnerability rate in public skills.
- Claude-plugin-marketplace style seed directory baked into Sprite base images for Fairy-curated skills (zero cold-start).

### Blast radius analysis

Sprite isolation already contains script execution — a malicious skill's `scripts/` can only affect its own per-session Sprite. The live risks are prompt injection (mitigated by content-size caps, strict name validation, per-user ownership) and `supporting_files` path traversal (mitigated by rejecting `..`/absolute paths in the validator). Multi-tenant filesystem concerns are non-issues because each session has a dedicated Sprite.

## Code References

| File | Tracks | Findings | Link |
|------|--------|----------|------|
| `src/fairy/models.py:221` | 1 | `Agent.skills = JSONField(default=list, blank=True)` — unvalidated | [L221](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/models.py#L221) |
| `src/fairy/models.py:252` | 1 | `AgentVersion.skills` mirror | [L252](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/models.py#L252) |
| `src/fairy/models.py:141-205` | 1, 5 | `Environment`/`EnvironmentVersion` template for Phase 2 `Skill` model | [L141-L205](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/models.py#L141-L205) |
| `src/fairy/views.py:468-485` | 1, 5 | `_validate_tools` pattern for the new `_validate_skills` | [L468-L485](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/views.py#L468-L485) |
| `src/fairy/views.py:488-510` | 1, 5 | `_validate_mcp_servers` — second validator template | [L488-L510](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/views.py#L488-L510) |
| `src/fairy/views.py:520, 553` | 1 | `CreateAgentRequest.skills` / `UpdateAgentRequest.skills` — where the validator plugs in | [L520](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/views.py#L520) |
| `src/fairy/views.py:224` | 1, 5 | `create_session` — where skills would be extracted and passed to `build_wrapper_script` | [L224](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/views.py#L224) |
| `src/fairy/views.py:338-404` | 1 | `send_prompt` — continue path that also needs skills materialization | [L338-L404](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/views.py#L338-L404) |
| `src/fairy/sprites_exec.py:196-206` | 1, 3 | `_build_mcp_section` — dispatch pattern for skills | [L196-L206](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/sprites_exec.py#L196-L206) |
| `src/fairy/sprites_exec.py:218-272` | 1, 3, 5 | `build_wrapper_script` — where the `_build_skills_section` call slots in | [L218-L272](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/sprites_exec.py#L218-L272) |
| `src/fairy/sprites_exec.py:86-117` | 1, 4 | `_build_clone_section` — precedent for any future git-fetch skills | [L86-L117](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/sprites_exec.py#L86-L117) |
| `src/fairy/runtimes.py:56-80` | 3 | Runtime CLI invocations; `claude-oauth` shares binary with `claude` | [L56-L80](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/runtimes.py#L56-L80) |

## External References

- Agent Skills open standard — https://agentskills.io
- Claude Code skills — https://code.claude.com/docs/en/skills
- Anthropic Skills overview — https://platform.claude.com/docs/en/docs/agents-and-tools/agent-skills/overview
- Anthropic Skills best practices — https://platform.claude.com/docs/en/docs/agents-and-tools/agent-skills/best-practices
- Skills Messages API guide — https://platform.claude.com/docs/en/build-with-claude/skills-guide
- Skills CRUD API — https://platform.claude.com/docs/en/api/beta/skills/create
- Codex skills — https://developers.openai.com/codex/skills
- Gemini skills — https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/skills.md
- Anthropic reference skills — https://github.com/anthropics/skills
- Curated list — https://github.com/travisvn/awesome-claude-skills
- Cross-platform skills — https://github.com/VoltAgent/awesome-agent-skills

## Open Questions

1. **`scripts/` supporting files in Phase 1** — the inline shape supports a single `content` string. Should Phase 1 reject items with a `scripts` / `supporting_files` key explicitly (to avoid silent data loss), or support a small `supporting_files: {path: content}` map now? Leaning reject, given the security surface.
2. **Continue-session materialization** — `send_prompt` builds a new wrapper each turn (`src/fairy/views.py:378`). Skills must be re-materialized there too, or omitted intentionally. Re-materializing is idempotent and simpler.
3. **Per-agent skill count cap** — Anthropic's Messages API caps at 8 skills; the Agent Skills spec has no explicit cap. Token budget grows with each skill's metadata. Propose a 20-skill cap per agent to match the mcp_servers limit (`src/fairy/views.py:508`).
4. **Skill name collisions** — when both inline and (future) `skill_id` entries coexist, duplicate `name` values would overwrite each other on disk. The validator should enforce name uniqueness within the list.
5. **Public-skill import risk** — given the 36.82% flaw rate in public skills, a Phase 3 `{"source": "git+https://..."}` mode should require SHA pinning, user acknowledgement of scope, and ideally an offline scan step before execution.
6. **Direct Messages API execution path** — if Fairy ever adds non-CLI execution (e.g., direct `POST /v1/messages` calls), managed-agents `skill_id` pass-through becomes viable. Phase 2 API design should leave room for a `{"type": "anthropic", "skill_id": "..."}` shape without committing to it now.
