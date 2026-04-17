---
date: 2026-04-17T11:25:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 072c6f3133a865eb8fea094dce29b9bee06996ae
branch: skills-phase-1
repository: ravi-hq/fairy
topic: "How should Fairy implement network isolation end-to-end (API → Sprites → tests)?"
tags: [research, team-research, environments, sprites, network-isolation, security]
status: complete
method: agent-team
team_size: 4
tracks: [sprites-api, data-model, http-surface, tests]
last_updated: 2026-04-17
last_updated_by: Claude Code
---

# Research: Network Isolation Implementation

**Date**: 2026-04-17T11:25:00-07:00
**Researcher**: Claude Code (team-research)
**Git Commit**: [`072c6f3`](https://github.com/ravi-hq/fairy/commit/072c6f3133a865eb8fea094dce29b9bee06996ae)
**Branch**: `skills-phase-1`
**Repository**: ravi-hq/fairy
**Method**: Agent team (4 specialist researchers)

## Research Question

> We have network isolation added to the API and Sprites supports network
> isolation at the API layer — so let's implement it and add it to tests and
> e2e tests.

Translation: what does "implement" mean in concrete terms — what models, views,
exec paths, and tests need to change?

## Summary

**This is a wire-up, not a net-new feature.** The Fairy API already accepts,
validates, persists, versions, and returns a `networking` field on Environment
(`networking_type` + `networking_config.allowed_hosts`). What is missing is the
actual enforcement: `src/fairy/views.py:214` calls `client.create_sprite(name)`
and never calls `sprite.update_network_policy()`, so isolation has no effect
regardless of what the user configures. The Sprites SDK surface is
`Sprite.update_network_policy(policy: NetworkPolicy)` where
`NetworkPolicy(rules: list[PolicyRule])` and each
`PolicyRule(domain, action, include)` has `action: "allow" | "deny"`. It is
applied post-creation, not as a `create_sprite` argument.

The implementation is a single translation helper and a call site in
`create_session`, plus a small mock-based unit test class mirroring
`TestSessionMcpIntegration` and an optional gated e2e enforcement suite that
actually tries a blocked host. **No migration, no model change, no new
HTTP field, no README surface change.**

## Research Tracks

### Track 1: Sprites API + sprites_exec integration
**Researcher**: `sprites-api-researcher`
**Scope**: `src/fairy/sprites_exec.py` (indirectly — integration lives in `views.py`), `src/fairy/views.py` exec path, Sprites SDK surface, historical research in `thoughts/research/2026-04-15-sprites-platform-research.md`.

#### Findings:
1. **Sprites has NO creation-time network isolation parameter.** `Sprite.update_network_policy(policy: NetworkPolicy) -> None` is the only API surface. It is applied *after* `create_sprite()` returns. Verified via `python3 -c "from sprites import Sprite; ..."`.
2. **Sprites SDK types**: `NetworkPolicy(rules: list[PolicyRule])` and `PolicyRule(domain: str | None, action: str | None, include: str | None)`. `action` is a string ("allow" / "deny"). The `include` field suggests shared preset allowlists exist on the Sprites side (not investigated further; not needed for this phase).
3. **Fairy never calls it.** `grep -n "update_network_policy\|NetworkPolicy"` returns zero hits in `src/`. The feature is fully unimplemented on the call side.
4. **Existing call site** ([`src/fairy/views.py:213-214`](src/fairy/views.py#L213-L214)): `sprite = client.create_sprite(name)` — takes only a name. The Sprite is then handed to `fs = sprite.filesystem()` and `build_wrapper_script` (L219-L240). A `sprite.update_network_policy(...)` call must go between `create_sprite` and `fs = sprite.filesystem()` (or anywhere before `sprite.command(...)` runs). It sits inside the `try/except SpriteError` that already handles cleanup on failure (L242-L247) — cleanest placement is right after L214 so a policy-apply error triggers the same sprite-delete path.
5. **Translation precedent**: the closest pattern is `_mcp_servers_to_specs` at [`src/fairy/views.py:50-63`](src/fairy/views.py#L50-L63), consumed at L235. A new `_networking_to_policy(networking_type, networking_config) -> NetworkPolicy | None` helper mirrors this shape — returns `None` when `networking_type == "unrestricted"`, otherwise a `NetworkPolicy` with allow-list rules and an implicit deny-all fallback.

### Track 2: Fairy data model placement
**Researcher**: `data-model-researcher`
**Scope**: `src/fairy/models.py` Environment/EnvironmentVersion, versioning signals, existing migrations, unit tests for versioning.

#### Findings:
1. **Fields already exist.** [`src/fairy/models.py:155-158`](src/fairy/models.py#L155-L158): `Environment.networking_type: CharField(choices=[("unrestricted", ...), ("limited", ...)], default="unrestricted")` and `Environment.networking_config: JSONField(default=dict)`. Both also mirror onto `EnvironmentVersion` at [`src/fairy/models.py:191-192`](src/fairy/models.py#L191-L192).
2. **Already versioned.** Environment updates snapshot these fields into `EnvironmentVersion` at [`src/fairy/views.py:1001-1002`](src/fairy/views.py#L1001-L1002), and changes to either field trigger a new version via the dirty-check at [`src/fairy/views.py:1096-1101`](src/fairy/views.py#L1096-L1101).
3. **Already integrated into admin.** `src/fairy/admin.py:106-113` lists `networking_type` in Environment admin.
4. **No new migration needed.** The fields are present, and the historical migration that added them is already applied. A zero-migration implementation is possible.
5. **Field shape is adequate for the Sprites API.** `allowed_hosts: list[str]` → `[PolicyRule(domain=h, action="allow") for h in allowed_hosts] + [PolicyRule(domain="*", action="deny")]` (exact rules pending verification of Sprites' wildcard semantics — see Open Questions).
6. **Placement decision**: Environment is correct. It is about the *runtime environment* the agent executes in, not about how the agent behaves — same reasoning that put `packages`, `env_vars`, and `setup_script` there. Agent-level would be wrong because multiple agents may share an env and isolation is an env concern.

#### Recommendation:
Zero data-model change. All work is in `views.py` wiring + tests. If a future phase wants per-rule granularity (arbitrary allow/deny lists, not just `allowed_hosts`), extend `networking_config` schema additively (still no migration — it's a `JSONField`).

### Track 3: HTTP surface (views, URLs, README)
**Researcher**: `http-surface-researcher`
**Scope**: `src/fairy/views.py`, `src/fairy/urls.py`, `README.md`.

#### Findings:
1. **Request validation is already in place.** `CreateEnvironmentRequest.networking` and `UpdateEnvironmentRequest.networking` Pydantic models at [`src/fairy/views.py:898, 913-922`](src/fairy/views.py#L898) and [L932, L948-954](src/fairy/views.py#L932), respectively. Inline `@field_validator` enforces `type in {"unrestricted", "limited"}` and `allowed_hosts` is a list.
2. **PUT omit semantics = keep-existing.** [`src/fairy/views.py:1096`](src/fairy/views.py#L1096): `if req.networking is not None:` — omitting the field on PUT preserves the prior value. This is the established pattern and no change is needed.
3. **Serialization round-trips today.** `_serialize_environment` at [`src/fairy/views.py:959-968`](src/fairy/views.py#L959-L968) and `_serialize_environment_version` at [L977-986](src/fairy/views.py#L977-L986) both emit `{"networking": {"type": ..., "allowed_hosts": [...]}}`.
4. **No README change required for this PR.** The README already documents `networking` on environment create/update. If we want to call out that isolation is *now enforced* (and what was aspirational before is now real), that's a one-paragraph nudge — not a structural rewrite.
5. **No other HTTP surface touched.** No OpenAPI schema file in the repo. No admin changes (already wired). SSE stream surface untouched — enforcement happens at Sprite creation, before the stream opens.

#### Minimal edit set:
- `src/fairy/views.py` — add `_networking_to_policy()` helper (near `_mcp_servers_to_specs`).
- `src/fairy/views.py:214` — after `client.create_sprite(name)`, call `sprite.update_network_policy(...)` when `environment_obj` has `networking_type == "limited"`. Wrap in the existing `try/except SpriteError` so failure triggers the cleanup path.
- `README.md` — optional one-line update noting enforcement is live (non-blocking).

### Track 4: Test patterns (unit + e2e)
**Researcher**: `tests-researcher`
**Scope**: `tests/conftest.py`, `tests/test_environments.py`, `tests/test_tools_mcp.py`, `tests/e2e/conftest.py`, `tests/e2e/test_environments.py`, `tests/e2e/test_sessions.py`.

#### Findings:
1. **Round-trip tests already exist.** `tests/e2e/test_environments.py:118-127` has `test_limited_networking` which creates `networking={"type": "limited", "allowed_hosts": ["api.example.com"]}` and asserts the response round-trips. Unit-level validation tests exist at `tests/test_environments.py:243` (`test_create_limited_networking_valid`) and `:275` (`test_create_invalid_networking_type_rejected`).
2. **Template for the new tests = `tests/test_tools_mcp.py::TestSessionMcpIntegration`.** It uses the `mock_sprites` fixture to capture calls to `sprite.command(...)` and asserts the wrapper script is built correctly. The parallel for networking is: capture `sprite.update_network_policy` and assert the `NetworkPolicy` it received has the right rules.
3. **Versioning tests**: `TestAgentVersioning` (e2e) and `tests/test_environments.py::test_environment_versions` cover "update bumps version, no-op keeps version". For networking specifically, `networking_type` or `networking_config` changes already trigger a new version (L1096-1101). A single unit test `test_update_networking_increments_version` is worth adding if not already present.
4. **E2E `@pytest.mark.slow` convention**: reserved for tests that spawn real Sprites via `POST /sessions`. Round-trip networking tests (create env, read back) are **not** slow. Enforcement tests **would** be slow.
5. **Enforcement e2e is possible but should be gated.** Shape: create an Environment with `networking={"type": "limited", "allowed_hosts": ["api.anthropic.com"]}`, spawn a session that runs `curl https://example.com` (a blocked host), assert the command fails. Should be gated by an env var (e.g. `E2E_NETWORK_ENFORCEMENT=1`) and marked `@pytest.mark.slow` — real Sprites, real money.
6. **No fixture changes needed.** `create_environment` in `tests/e2e/conftest.py:259` is a `**kw` passthrough — `networking=...` already works.

#### Test plan outline (net-new tests):
*Unit — new class in `tests/test_environments.py` or new `tests/test_networking.py`:*
- `TestSessionNetworkingIntegration::test_limited_networking_applies_policy_to_sprite` — uses `mock_sprites`, asserts `update_network_policy` was called with allow-rules for `allowed_hosts` + deny-all wildcard.
- `TestSessionNetworkingIntegration::test_unrestricted_networking_does_not_call_update_policy` — asserts `update_network_policy` is never invoked for default environments.
- `TestSessionNetworkingIntegration::test_session_without_environment_does_not_call_update_policy` — covers the no-environment code path.
- `TestSessionNetworkingIntegration::test_policy_apply_failure_cleans_up_sprite` — force `update_network_policy` to raise, assert `delete_sprite` is called (tests the try/except cleanup path).

*Unit — `tests/test_environments.py::TestUpdateEnvironment` additions (filling an existing coverage gap):*
- `test_update_networking_type_increments_version` — PUT changing `networking_type` from `unrestricted` → `limited`, assert version 1→2 and the new `EnvironmentVersion` row carries the new type. This covers the currently-untested dirty-check at `src/fairy/views.py:1096-1101` — a gap that predates this work.
- `test_update_networking_no_change_keeps_version` — PUT with same `networking_type` + `networking_config`, assert version stays at 1.

*E2E — `tests/e2e/test_sessions.py` (gated + slow):*
- `TestNetworkEnforcement::test_limited_networking_blocks_disallowed_host` — `@pytest.mark.slow`, gated on `E2E_NETWORK_ENFORCEMENT=1`.
- `TestNetworkEnforcement::test_limited_networking_allows_listed_host` — same gating.

## Cross-Track Discoveries

- **The "feature" is already half-built.** All three downstream tracks (data model, HTTP surface, existing tests) returned a variant of "this already works at its level." The one-line summary of the whole research: the only truly missing piece is the 3–5 lines of code that translate `Environment.networking_*` into a `sprite.update_network_policy()` call.
- **Why the dead stub exists** is worth a look (likely: landed ahead of Sprites SDK support, or the researcher / plan `thoughts/research/2026-04-15-sprites-platform-research.md` predates SDK availability). Not blocking — noted as historical context.
- **The test template for the new wiring already exists.** `TestSessionMcpIntegration` in `tests/test_tools_mcp.py` is the exact shape: use `mock_sprites`, assert a Sprite method was called with the expected payload. Copying this pattern is cheaper than designing new test scaffolding.

## Code References

| File | Purpose | Lines |
|------|---------|-------|
| `src/fairy/models.py` | `Environment.networking_type`, `networking_config` | 155-158 |
| `src/fairy/models.py` | `EnvironmentVersion` mirror | 191-192 |
| `src/fairy/views.py` | Create-sprite call site (where policy-apply must go) | 213-214 |
| `src/fairy/views.py` | `_mcp_servers_to_specs` (translation helper template) | 50-63 |
| `src/fairy/views.py` | `CreateEnvironmentRequest.networking` validator | 898, 913-922 |
| `src/fairy/views.py` | `UpdateEnvironmentRequest.networking` validator | 932, 948-954 |
| `src/fairy/views.py` | Serialize environment/version response | 959-968, 977-986 |
| `src/fairy/views.py` | Snapshot + version-bump on update | 1001-1002, 1096-1101 |
| `src/fairy/admin.py` | Admin displays `networking_type` | 106-113 |
| `tests/test_tools_mcp.py` | `TestSessionMcpIntegration` template | (whole class) |
| `tests/test_environments.py` | Existing networking validation tests | 243, 275 |
| `tests/e2e/test_environments.py` | Existing round-trip test | 118-127 |
| `tests/e2e/conftest.py` | `create_environment` factory (no changes needed) | 259 |

## Architecture Insights

- **Translation helpers live in `views.py` next to the exec path**, not in `sprites_exec.py`. The pattern: a stateless `_*_to_specs()` function that takes model fields and returns SDK-shaped objects, called once inside the `create_session` `try/except`.
- **Post-creation SDK configuration** is the Sprites idiom for resources that aren't `create_sprite()` parameters (network policy here; filesystem writes via `sprite.filesystem()`; commands via `sprite.command()`). When adding new Sprite-side config in the future, assume "configure after create" rather than "parameterize create."
- **Failure in the setup chain triggers sprite cleanup** via `client.delete_sprite(name)` at L244. Any new SDK call inside the try block inherits this behavior — the `update_network_policy()` call should be inside this block so a policy-apply failure still cleans up the orphaned Sprite.

## Historical Context

- `thoughts/research/2026-04-15-sprites-platform-research.md` — the platform research that informed the Environment model shape. Predates the SDK's `update_network_policy` availability (or didn't surface it), which is likely why the field shipped as a stub.
- `thoughts/plans/2026-04-16-environment-model.md` — the plan that introduced `networking_type` / `networking_config`.

## Related Research

- `thoughts/research/2026-04-16-agent-tool-enforcement-e2e.md` — established the "Fairy field stored but not enforced" pattern that this research follows. That case was resolved for `tools` in PR #7; this is the equivalent resolution for `networking`.

## Open Questions

1. **Wildcard semantics.** Does Sprites interpret `PolicyRule(domain="*", action="deny")` as "deny everything not explicitly allowed"? If not, the deny-all fallback needs a different encoding. Test by hitting Sprites directly or reading SDK docs before landing.
2. **Rule ordering.** If the Sprites engine is first-match-wins, `[allow *.anthropic.com, deny *]` works. If it's last-match-wins or any other semantics, the rule list order matters. Needs verification.
3. **`PolicyRule.include` field.** There is a third parameter (`include: str | None`). Likely references shared preset allowlists (e.g. "claude-default"). Not needed for phase 1 but worth knowing exists — future Environment schema could expose it as `networking_config.include: ["preset-name"]`.
4. **Is an API key required for `update_network_policy`?** Unknown whether it's gated by the same credentials as `create_sprite`. If yes, already handled; if no, need to check.
5. **Behavior for `limited` with empty `allowed_hosts`.** Should this be "deny all" (strictest) or rejected at validation time as a likely user error? Current validator accepts it. Recommend: treat as "deny all" (coherent and potentially useful for fully-offline agents).
