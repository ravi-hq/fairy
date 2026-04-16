---
date: 2026-04-16T05:35:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 3c6a70f3212d00cf5a09c819a4a131dcdf9ec5ca
branch: main
repository: ravi-hq/fairy
topic: "Adding GitHub repository cloning as session context"
tags: [research, team-research, github, git, resources, api-design, security]
status: complete
method: agent-team
team_size: 4
tracks: [sprites-sdk, api-design, execution-pipeline, security]
last_updated: 2026-04-16
last_updated_by: Claude Code
---

# Research: Adding GitHub Repository Cloning as Session Context

**Date**: 2026-04-16
**Researcher**: Claude Code (team-research)
**Git Commit**: [`3c6a70f`](../../)
**Branch**: `main`
**Repository**: ravi-hq/fairy
**Method**: Agent team (4 specialist researchers)

## Research Question

How should Fairy add the ability to clone GitHub repositories into Sprite containers as context for agent sessions? This includes accepting a `GITHUB_TOKEN`, modeling the API after the Claude Managed Agents `resources[]` pattern, and ensuring secure token handling throughout the system.

## Summary

Sprites already have git pre-installed and full outbound network access — cloning repos is straightforward via shell commands in the existing wrapper script. The API should add an optional `resources[]` field to `POST /sessions` with `github_repository` entries (url, mount_path, authorization_token). Tokens should be stored encrypted using the existing Fernet pattern and injected via a `.git-credentials` file (not URL embedding) to prevent leakage in process listings. The existing error handling pipeline (set -euo pipefail + ExecError catch) handles clone failures without new code.

## Research Tracks

### Track 1: Sprites SDK & Container Capabilities
**Researcher**: sprites-researcher
**Scope**: sprites-py SDK source, prior Sprites research docs, container environment

#### Findings:
1. **Git is pre-installed** — Sprites run Ubuntu 25.10 microVMs with git, curl, vim pre-installed. No installation step needed. (`thoughts/research/2026-04-15-sprites-platform-research.md`)
2. **Full outbound network access** — Sprites have unrestricted outbound networking by default. github.com is reachable for HTTPS cloning. Network policies can restrict access post-clone.
3. **sprite.command() for cloning** — `sprite.command("bash", "-c", "git clone ...")` works. Do NOT use `env=` parameter — it puts values in WebSocket URL query params (server log leakage). (`src/fairy/sprites_exec.py:6-31`)
4. **Filesystem persists across hibernation** — If a Sprite is reused, cloned repos persist. Good for warm pools but tokens in wrapper scripts also persist — must overwrite on each use.
5. **Checkpoints for pre-cloned state** — `sprite.create_checkpoint()` can snapshot after clone for ~300ms restore instead of re-cloning. Future optimization opportunity.
6. **Network policy lockdown** — `sprite.update_network_policy()` can deny github.com after cloning as defense-in-depth, preventing the agent from exfiltrating data back.

### Track 2: API Surface Design
**Researcher**: api-researcher
**Scope**: views.py, urls.py, models.py, Managed Agents API pattern

#### Findings:
1. **`GitHubRepoResource` Pydantic model** — New schema with `type` (literal "github_repository"), `url`, `mount_path` (optional, defaults to `/workspace/<repo-name>`), `authorization_token` (optional, for private repos). Validation: URL must match `https://github.com/<org>/<repo>`, mount_path must be absolute, token must start with `ghp_`, `github_pat_`, or `ghs_`.
2. **`RunRequest.resources` field** — Optional `list[GitHubRepoResource]`, default empty list. Fully backward-compatible — existing clients sending no `resources` field continue to work.
3. **`SessionResource` DB model** — New model with FK to `AgentSession`, storing `resource_type`, `url`, `mount_path`, `encrypted_token` (nullable BinaryField). Uses same `encrypt()`/`decrypt()` pattern as `UserRuntimeKey`. (`src/fairy/models.py:51-75`)
4. **No new URL endpoints for MVP** — Resources are attached at session creation and immutable. The existing `POST /sessions` + `GET /sessions/<id>` endpoints are sufficient. Resource management endpoints (list, update/rotate token) can come later.
5. **Token is write-only** — `authorization_token` is accepted in requests but NEVER returned in any API response. Only `type`, `url`, and `mount_path` appear in responses.
6. **Validation rules** — Soft cap of 10 resources per session, reject duplicate mount_paths in same request, mount_path must not be `/home/sprite` root (conflicts with existing git init).

### Track 3: Execution Pipeline
**Researcher**: pipeline-researcher
**Scope**: sprites_exec.py, stream.py, runtimes.py

#### Findings:
1. **Clone in wrapper script, before `exec`** — Add git clone commands between the "setup working directory" section and the `exec {cmd}` line. This is the natural insertion point. (`src/fairy/sprites_exec.py:16-31`)
2. **`--depth=1` shallow clone by default** — Dramatically faster for large repos. Most agents don't need full git history. Can be made configurable per-resource later.
3. **`--quiet` flag** — Suppresses progress noise but errors still print to stderr. Clone errors flow through existing `TaggingQueueWriter` → `AgentSessionLog` pipeline unchanged. (`src/fairy/stream.py:30-47`)
4. **`set -euo pipefail` handles clone failures** — A failed clone aborts the script with non-zero exit. The existing `ExecError` catch in `run_session_background()` records the exit code and sets `status="failed"`. No new error handling code needed. (`src/fairy/stream.py:76-77`)
5. **No changes to stream.py or runtimes.py** — Clone output flows through the existing capture pipeline. `RuntimeConfig` doesn't need to know about repos.
6. **Both `create_session` and `send_prompt` need updates** — Both views call `build_wrapper_script()` and need to pass the `repos` parameter. For `send_prompt`, resources are loaded from DB via the `SessionResource` model.

### Track 4: Security & Token Lifecycle
**Researcher**: security-researcher
**Scope**: crypto.py, models.py, token handling patterns

#### Findings:
1. **Encrypted at-rest storage** — Use the existing Fernet encryption pattern from `crypto.py` (derives AES key from Django `SECRET_KEY`). Store as `encrypted_token` BinaryField on `SessionResource`, same pattern as `UserRuntimeKey.encrypted_key`. (`src/fairy/crypto.py:1-19`, `src/fairy/models.py:71-75`)
2. **`.git-credentials` file preferred over URL embedding** — Embedding token in the clone URL (`https://token@github.com/...`) exposes it in `ps aux` output inside the Sprite. Instead, write a `.git-credentials` file via `sprite.filesystem()` and configure `git credential.helper=store`:
   ```bash
   echo "https://${GITHUB_TOKEN}:x-oauth-basic@github.com" > /tmp/.git-credentials
   git config --global credential.helper 'store --file=/tmp/.git-credentials'
   git clone https://github.com/org/repo /workspace/repo
   rm -f /tmp/.git-credentials
   ```
3. **Token cleanup after clone** — Remove `.git-credentials` and unset `GITHUB_TOKEN` env var after all clones complete. The agent process should never see the token.
4. **Never log tokens** — The wrapper script approach (via `sprite.filesystem()`) means the token is written to a file on the Sprite, not passed via `env=` (which leaks to WebSocket URL params) or command-line args (which leak to `ps aux`).
5. **No token rotation for MVP** — Resources are immutable after session creation. Token rotation endpoint (`PATCH /sessions/<id>/resources/<id>`) can come later, matching the Managed Agents pattern.
6. **Network policy as defense-in-depth** — After cloning, lock down outbound to only the AI provider domain. Prevents the agent from making further GitHub API calls or pushing code.

## Cross-Track Discoveries

- **Token injection method** — Tracks 3 and 4 converged on a key design decision: use `.git-credentials` file rather than URL embedding. Track 3 initially proposed URL embedding for simplicity, but Track 4 identified the process listing leakage risk. The `.git-credentials` approach adds ~3 lines to the wrapper script but eliminates a class of token exposure.
- **Existing error handling is sufficient** — Tracks 1 and 3 independently confirmed that the existing `set -euo pipefail` + `ExecError` + `AgentSessionLog` pipeline handles clone failures without new code. This significantly reduces implementation scope.
- **Wrapper script is the right integration point** — Tracks 1, 3, and 4 all agreed: don't use the SDK's `sprite.command()` for individual clone operations. Keep everything in the wrapper script for atomicity (all-or-nothing setup) and simplicity.

## Code References

| File | Tracks | Relevance |
|------|--------|-----------|
| `src/fairy/views.py` | 2, 3 | Add `resources` to `RunRequest`, pass to `build_wrapper_script`, persist `SessionResource` |
| `src/fairy/sprites_exec.py` | 1, 3, 4 | Add `repos` param, generate clone section with `.git-credentials` |
| `src/fairy/models.py` | 2, 4 | Add `SessionResource` model with encrypted token storage |
| `src/fairy/crypto.py` | 4 | Reuse existing Fernet encrypt/decrypt for GitHub tokens |
| `src/fairy/stream.py` | 3 | No changes needed — clone output flows through existing pipeline |
| `src/fairy/runtimes.py` | 3 | No changes needed |
| `src/fairy/urls.py` | 2 | No changes for MVP |

## Architecture Insights

The design follows a clean separation:
- **API layer** (views.py) — Validates resources, persists to DB, passes to script builder
- **Script builder** (sprites_exec.py) — Generates clone commands with secure credential handling
- **Execution layer** (stream.py) — Unchanged, handles clone output via existing pipeline
- **Storage layer** (models.py + crypto.py) — Encrypted token storage, same pattern as runtime API keys

The Managed Agents API influence is visible in the `resources[]` array pattern, write-only `authorization_token`, and the future path toward resource management endpoints. But the MVP is deliberately minimal: resources set at creation, immutable, no rotation.

## Proposed Implementation Order

1. **Migration**: Add `SessionResource` model + migration
2. **Pydantic schemas**: Add `GitHubRepoResource` and update `RunRequest`
3. **sprites_exec.py**: Update `build_wrapper_script()` with clone section and `.git-credentials`
4. **views.py**: Wire resources through `create_session` and `send_prompt`
5. **Tests**: Add tests for validation, clone script generation, token handling
6. **Optional**: Network policy lockdown after clone, resource list endpoint

## Open Questions

1. **Shallow clone depth** — Should `--depth=1` be the only option, or should the API accept a `depth` parameter per-resource?
2. **Branch/tag/commit support** — Should resources accept a `ref` field for cloning a specific branch or tag? (Managed Agents API doesn't have this, but it's a common need.)
3. **Clone timeout** — Large repos may take a long time to clone. Should there be a per-resource timeout, or does the session-level timeout suffice?
4. **Sprite checkpointing** — For frequently-used repos, checkpointing after clone could give ~300ms restore. Worth implementing as a follow-up optimization?
5. **Network policy lockdown** — Should post-clone network lockdown be automatic or opt-in?
6. **`api_key` field removal** — Currently `RunRequest` requires `api_key` directly. Long-term, should this also move into a `resources`-style pattern for consistency with the Managed Agents model?
