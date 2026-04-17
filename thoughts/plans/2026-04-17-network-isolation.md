# Network Isolation — Implementation Plan

## Overview

Wire the existing `Environment.networking_type` / `networking_config` fields to
the Sprites `update_network_policy` API so that the `"limited"` networking mode
is actually enforced. Today the fields are accepted, validated, persisted,
versioned, and round-tripped through the HTTP layer — but `create_session`
never calls `sprite.update_network_policy()`, so the policy has no effect.

This is a **wire-up plan**, not a new-feature plan. Zero schema changes. Zero
new HTTP fields. The change surface is one translation helper, one call site,
and a focused test class.

## Research Summary

Full research: `thoughts/research/2026-04-17-network-isolation.md` (4-track
agent team: Sprites API, data model, HTTP surface, tests).

### Key Discoveries

- `Environment.networking_type` + `networking_config` already exist at [`src/fairy/models.py:155-158`](src/fairy/models.py#L155-L158) and mirror onto `EnvironmentVersion` at [L191-192](src/fairy/models.py#L191-L192).
- Validation, serialization, version-bump on change, and admin integration are all already wired in `src/fairy/views.py` (lines 898-961, 959-986, 1001-1002, 1021-1031, 1096-1101) and `src/fairy/admin.py:106-113`.
- `Sprite.update_network_policy(NetworkPolicy(rules=[PolicyRule(domain, action, include)]))` is the SDK surface. Applied *post*-creation via `POST /v1/sprites/{name}/policy/network`.
- Sprites semantics (from [sprites.dev/api/sprites/policies](https://sprites.dev/api/sprites/policies#set-network-policy) and the user-confirmed summary): DNS-based filtering, supports exact/wildcard/`*`/preset `include`; changes apply immediately and terminate existing connections to newly-blocked domains; blocked lookups return DNS `REFUSED`.
- Official example pattern matches our target encoding: `[{allow, github.com}, {allow, *.npmjs.org}, {deny, *}]` — allow-list plus catch-all deny.
- The dirty-check at [`src/fairy/views.py:1096-1101`](src/fairy/views.py#L1096-L1101) that version-bumps on networking changes is currently untested.
- Test template: `tests/test_tools_mcp.py::TestSessionMcpIntegration` — mocks `_get_client`, asserts Sprite calls.

## Current State Analysis

**What works today:**
- Create/update environments with `networking: {"type": "limited", "allowed_hosts": [...]}`.
- Response round-trips the same shape.
- Changes to networking fields increment `Environment.version` and create an `EnvironmentVersion` snapshot.
- Admin UI shows `networking_type`.

**What doesn't work:**
- `src/fairy/views.py:214` creates a Sprite with `client.create_sprite(name)` and never applies a network policy — `sprite.update_network_policy()` is not called anywhere in the codebase.
- Consequence: any environment with `networking_type="limited"` is silently equivalent to `"unrestricted"` at runtime.

## Desired End State

- `POST /sessions` with an Environment whose `networking_type="limited"` results in `sprite.update_network_policy()` being called with an allow-list of `networking_config["allowed_hosts"]` + a catch-all `(domain="*", action="deny")`.
- `networking_type="unrestricted"` (default) results in no policy call — Sprites default-allow applies.
- Policy-apply failures trigger the existing Sprite cleanup path (same try/except as other setup failures).
- Unit tests lock in the call shape with `mock_sprites`.
- Optional e2e enforcement tests (gated) verify real Sprite blocks disallowed hosts.

**Verification:** a new session created in a `limited` environment with `allowed_hosts=["api.anthropic.com"]` can reach `api.anthropic.com` and cannot reach `example.com`.

## What We're NOT Doing

- Adding a new field. The existing `networking_type` / `networking_config` are sufficient.
- Exposing Sprites' `include` (preset bundles) in the API surface. Phase 2 at earliest.
- Supporting explicit deny rules or mixed allow/deny lists. Today's schema is allow-list-only and this plan preserves that.
- Changing response shapes or README payload docs. (One prose nudge to README only, see Phase 1.)
- Migrating existing environments. `networking_type` defaults to `"unrestricted"` — no behavior change for anything that doesn't opt in to `"limited"`.

## Implementation Approach

Three concrete changes, all in a single domain (backend Python):

1. **Translation helper + call site** in `src/fairy/views.py`. Mirror the `_mcp_servers_to_specs` pattern: a pure function that takes model fields and returns an SDK object, called once inside the `create_session` try/except.
2. **Unit tests** in a new file `tests/test_networking_wiring.py` mirroring `TestSessionMcpIntegration`. Plus two fill-in-the-gap tests in `tests/test_environments.py` for the previously-untested version-bump path.
3. **Optional e2e enforcement tests** in `tests/e2e/test_sessions.py`, `@pytest.mark.slow`, gated on `E2E_NETWORK_ENFORCEMENT=1`.

Because scope is single-file single-domain, this plan has one phase with optional sub-phase for e2e enforcement.

## File Ownership Map

| File | Phase | Change Type |
|------|-------|-------------|
| `src/fairy/views.py` | 1 | modify (add helper, add call site) |
| `tests/test_networking_wiring.py` | 1 | create |
| `tests/test_environments.py` | 1 | modify (add version-bump tests) |
| `README.md` | 1 | modify (one-paragraph nudge) |
| `tests/e2e/test_sessions.py` | 2 (optional) | modify (add enforcement class) |

No cross-file conflict risk — single-track execution.

---

## Phase 1: Wire the policy + lock in with unit tests

### Overview

Make `networking_type="limited"` actually enforce isolation. Cover with mocked
unit tests and close the existing version-bump test gap.

### Changes Required

#### 1. Translation helper — `src/fairy/views.py`

Add next to `_mcp_servers_to_specs` (around L50):

```python
def _environment_to_network_policy(env: Environment | None) -> "NetworkPolicy | None":
    """Return a NetworkPolicy for `limited` environments, else None.

    None signals "do not call update_network_policy" — Sprites default-allow applies.
    """
    if env is None or env.networking_type != "limited":
        return None
    allowed_hosts = env.networking_config.get("allowed_hosts", []) if env.networking_config else []
    rules = [PolicyRule(domain=h, action="allow") for h in allowed_hosts]
    rules.append(PolicyRule(domain="*", action="deny"))
    return NetworkPolicy(rules=rules)
```

Add the import at the top of `views.py` next to existing `sprites` imports:

```python
from sprites import NetworkPolicy, PolicyRule  # add to existing sprites import block
```

#### 2. Call site — `src/fairy/views.py:214`

Inside the existing `try/except SpriteError` block in `create_session`,
immediately after `sprite = client.create_sprite(name)` and before
`fs = sprite.filesystem()`:

```python
try:
    sprite = client.create_sprite(name)
except SpriteError as e:
    return JsonResponse({"detail": f"Failed to create Sprite: {e}"}, status=502)

try:
    policy = _environment_to_network_policy(environment_obj)
    if policy is not None:
        sprite.update_network_policy(policy)

    fs = sprite.filesystem()
    # ... rest unchanged
except SpriteError as e:
    try:
        client.delete_sprite(name)
    except SpriteError:
        logger.warning("Failed to cleanup Sprite %s", name, exc_info=True)
    return JsonResponse({"detail": f"Failed to prepare Sprite: {e}"}, status=502)
```

Placement rationale: inside the existing `try/except SpriteError` block so
a policy-apply failure triggers the same cleanup path as other setup
failures. Before `fs = sprite.filesystem()` so isolation takes effect before
any filesystem writes happen (belt-and-suspenders — the filesystem SDK
doesn't make outbound calls, but ordering matches security-first intuition).

#### 3. Unit tests — `tests/test_networking_wiring.py` (new file)

Mirror `tests/test_tools_mcp.py::TestSessionMcpIntegration` structure. Use
the existing `mock_sprites` fixture pattern; extend the mock Sprite to
record `update_network_policy` calls.

```python
class TestSessionNetworkingIntegration:
    def test_limited_networking_applies_policy_to_sprite(self, client, mock_sprites):
        # env with networking_type="limited", allowed_hosts=["api.anthropic.com"]
        # POST /sessions
        # assert mock_sprites.sprite.update_network_policy.call_count == 1
        # inspect call_args[0][0] (NetworkPolicy) — rules should be
        # [PolicyRule("api.anthropic.com", "allow"), PolicyRule("*", "deny")]
        ...

    def test_unrestricted_networking_skips_policy_call(self, client, mock_sprites):
        # default env, POST /sessions
        # assert mock_sprites.sprite.update_network_policy.call_count == 0
        ...

    def test_session_without_environment_skips_policy_call(self, client, mock_sprites):
        # agent without environment_id, POST /sessions
        # assert update_network_policy not called
        ...

    def test_policy_apply_failure_cleans_up_sprite(self, client, mock_sprites):
        # limited env; mock_sprites.sprite.update_network_policy raises SpriteError
        # POST /sessions → assert 502 response
        # assert mock_sprites.client.delete_sprite was called with the sprite name
        ...

    def test_limited_with_empty_allowed_hosts_denies_all(self, client, mock_sprites):
        # limited env with allowed_hosts=[]
        # POST /sessions
        # assert NetworkPolicy has exactly one rule: (domain="*", action="deny")
        ...
```

#### 4. Close existing coverage gap — `tests/test_environments.py`

Add to `TestUpdateEnvironment` (or adjacent):

```python
def test_update_networking_type_increments_version(self, client, environment):
    # PUT /environments/{id} with networking={"type": "limited", "allowed_hosts": ["x.com"]}
    # assert 200; assert env.version incremented 1 → 2
    # assert new EnvironmentVersion row exists with networking_type="limited"
    ...

def test_update_networking_no_change_keeps_version(self, client, environment):
    # PUT with same networking value as current
    # assert env.version unchanged
    ...
```

These cover the currently-untested dirty-check at `views.py:1096-1101`.

#### 5. README prose nudge — `README.md`

Current prose (L7 area) says environments include "packages, env vars, setup
scripts, networking" without noting enforcement status. Add one sentence:

> Setting `networking.type` to `"limited"` now enforces DNS-based allow-list
> isolation at session start — domains outside `allowed_hosts` are blocked
> (DNS `REFUSED`) for the lifetime of the session.

If the README already implies enforcement, skip this edit.

### Success Criteria

#### Automated Verification

- [ ] `make lint` passes (no new ruff violations).
- [ ] `make test` passes:
  - New `tests/test_networking_wiring.py::TestSessionNetworkingIntegration` class — all 5 tests green.
  - New `tests/test_environments.py` version-bump tests — green.
  - No existing tests broken.
- [ ] `make fmt` produces no diff after formatting (code is already formatted).

#### Manual Verification

- [ ] Read the diff of `views.py` — confirm the `update_network_policy` call is inside the correct try/except block and the `None` return from the helper cleanly short-circuits (no spurious calls in the unrestricted path).
- [ ] Confirm no migration was generated by accident: `find src/fairy/migrations/ -newer thoughts/plans/2026-04-17-network-isolation.md` returns empty.

**Gate**: Pause after Phase 1 to decide whether Phase 2 enforcement e2e tests are worth the cost. They require a live Sprites deployment and a FAIRY_API_TOKEN and spawn real Sprites.

---

## Phase 2 (optional): Real-Sprite enforcement e2e tests

### Overview

Prove that enforcement actually works against a live Sprite, not just that
our code calls the SDK method. Gated so `make test-e2e-fast` doesn't run it.

### Dependencies

- Phase 1 complete and merged.
- Live Sprites deployment accessible.
- `FAIRY_API_TOKEN` + `E2E_NETWORK_ENFORCEMENT=1` in env.

### Changes Required

#### 1. e2e tests — `tests/e2e/test_sessions.py`

```python
@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("E2E_NETWORK_ENFORCEMENT") != "1",
    reason="Set E2E_NETWORK_ENFORCEMENT=1 to run network enforcement tests (real Sprites, slow)",
)
class TestNetworkEnforcement:
    def test_limited_networking_blocks_disallowed_host(
        self, api, create_agent, create_environment, create_session
    ):
        env = create_environment(networking={"type": "limited", "allowed_hosts": ["api.anthropic.com"]})
        agent = create_agent(
            system="Run: curl -s --max-time 5 https://example.com/ -o /dev/null; echo EXIT=$?",
            environment_id=env["id"],
        )
        session = create_session(agent_id=agent["id"], prompt="run")
        result = api.run_session(session["id"])
        # Expect non-zero exit in output (DNS REFUSED → curl fails)
        assert "EXIT=0" not in stream_all_output(result.events)
        assert "EXIT=" in stream_all_output(result.events)  # curl did run

    def test_limited_networking_allows_listed_host(
        self, api, create_agent, create_environment, create_session
    ):
        env = create_environment(networking={"type": "limited", "allowed_hosts": ["api.anthropic.com"]})
        agent = create_agent(
            system="Run: curl -s --max-time 10 https://api.anthropic.com/ -o /dev/null; echo EXIT=$?",
            environment_id=env["id"],
        )
        session = create_session(agent_id=agent["id"], prompt="run")
        result = api.run_session(session["id"])
        # api.anthropic.com at `/` will likely 4xx but the connection succeeds
        out = stream_all_output(result.events)
        assert "EXIT=0" in out or "HTTP/" in out  # connection established

    def test_unrestricted_networking_reaches_internet(
        self, api, create_agent, create_environment, create_session
    ):
        env = create_environment()  # default unrestricted
        agent = create_agent(
            system="Run: curl -s --max-time 5 https://example.com/ -o /dev/null; echo EXIT=$?",
            environment_id=env["id"],
        )
        session = create_session(agent_id=agent["id"], prompt="run")
        result = api.run_session(session["id"])
        assert "EXIT=0" in stream_all_output(result.events)
```

### Success Criteria

#### Automated Verification

- [ ] `make test-e2e` with `E2E_NETWORK_ENFORCEMENT=1` runs the new class and passes.
- [ ] `make test-e2e-fast` (without the env var) skips the new class.

#### Manual Verification

- [ ] Inspect Sprites dashboard / logs for one of the blocked-host test runs and confirm a DNS REFUSED event fired.

---

## Testing Strategy

### Automated

- Unit tests in `tests/test_networking_wiring.py` lock in the SDK call shape without any real Sprite.
- Unit tests in `tests/test_environments.py` cover the version-bump path.
- Gated e2e tests in `tests/e2e/test_sessions.py` prove end-to-end enforcement when explicitly opted into.

### Manual Testing Steps

1. Start dev server: `make dev`.
2. Create an environment via curl with `networking={"type": "limited", "allowed_hosts": ["api.anthropic.com"]}`.
3. Create an agent referencing that environment.
4. Start a session with a prompt like "curl https://example.com" — observe the session log shows connection failure.
5. Start a session with "curl https://api.anthropic.com" — observe the connection succeeds.

## Performance Considerations

- `update_network_policy` adds one extra HTTP call to the Sprites control plane per session-create for limited environments. Negligible.
- Unrestricted environments skip the call entirely — zero impact on the default path.
- No DB impact.

## References

- Research: [`thoughts/research/2026-04-17-network-isolation.md`](../research/2026-04-17-network-isolation.md)
- Sprites policy API: [sprites.dev/api/sprites/policies#set-network-policy](https://sprites.dev/api/sprites/policies#set-network-policy)
- Comparable wire-up pattern: `_mcp_servers_to_specs` at [`src/fairy/views.py:50-63`](../../src/fairy/views.py#L50-L63) and its call site at [L235](../../src/fairy/views.py#L235).
- Related work: `thoughts/research/2026-04-16-agent-tool-enforcement-e2e.md` (same "field stored but not enforced" shape, resolved for `tools` in PR #7).
