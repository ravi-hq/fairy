"""Direct unit tests for `build_network_policy`.

Mutation-tested. Each test pins one mutation-killable property of the
`NetworkPolicy` returned for a session:

  - `env=None` and `env.networking_type != "limited"` skip the policy.
  - When `networking_type == "limited"`, allow rules come from
    `networking_config.allowed_hosts` in input order, followed by exactly
    one `*`/deny rule. A reorder mutant that puts the deny rule first
    silently lets everything through.
  - The final rule is always `domain="*", action="deny"` regardless of
    how many allow-listed hosts are present.
  - Missing/None `networking_config` collapses to a single `*`/deny rule.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them. ``Environment`` is duck-typed via
``SimpleNamespace``.
"""

from types import SimpleNamespace

from sprites import NetworkPolicy, PolicyRule

from agent_on_demand.session_service.network_policy import build_network_policy


def _env(networking_type="limited", networking_config=None):
    return SimpleNamespace(
        networking_type=networking_type,
        networking_config=networking_config,
    )


# ---------- env=None / non-limited networking ----------


def test_none_env_returns_none():
    assert build_network_policy(None) is None


def test_unrestricted_returns_none():
    assert build_network_policy(_env(networking_type="unrestricted")) is None


def test_open_returns_none():
    # Defensive: any value other than "limited" is treated as no policy.
    assert build_network_policy(_env(networking_type="open")) is None


def test_empty_string_networking_type_returns_none():
    assert build_network_policy(_env(networking_type="")) is None


# ---------- limited networking returns NetworkPolicy ----------


def test_limited_returns_network_policy_instance():
    env = _env(networking_config={"allowed_hosts": ["github.com"]})
    policy = build_network_policy(env)
    assert isinstance(policy, NetworkPolicy)


def test_limited_with_two_allowed_hosts_emits_three_rules_in_order():
    env = _env(networking_config={"allowed_hosts": ["github.com", "pypi.org"]})
    policy = build_network_policy(env)
    assert policy.rules == [
        PolicyRule(domain="github.com", action="allow"),
        PolicyRule(domain="pypi.org", action="allow"),
        PolicyRule(domain="*", action="deny"),
    ]


def test_limited_preserves_input_order_of_allowed_hosts():
    env = _env(networking_config={"allowed_hosts": ["pypi.org", "github.com"]})
    policy = build_network_policy(env)
    domains = [r.domain for r in policy.rules]
    # Input order is preserved (no sort applied), and deny is last.
    assert domains == ["pypi.org", "github.com", "*"]


def test_limited_with_one_allowed_host_emits_two_rules():
    env = _env(networking_config={"allowed_hosts": ["api.anthropic.com"]})
    policy = build_network_policy(env)
    assert policy.rules == [
        PolicyRule(domain="api.anthropic.com", action="allow"),
        PolicyRule(domain="*", action="deny"),
    ]


# ---------- empty / missing allowed_hosts ----------


def test_limited_with_empty_allowed_hosts_emits_only_deny():
    env = _env(networking_config={"allowed_hosts": []})
    policy = build_network_policy(env)
    assert policy.rules == [PolicyRule(domain="*", action="deny")]


def test_limited_with_none_networking_config_emits_only_deny():
    env = _env(networking_config=None)
    policy = build_network_policy(env)
    assert policy.rules == [PolicyRule(domain="*", action="deny")]


def test_limited_with_empty_networking_config_emits_only_deny():
    env = _env(networking_config={})
    policy = build_network_policy(env)
    assert policy.rules == [PolicyRule(domain="*", action="deny")]


# ---------- final rule is always *-deny ----------


def test_final_rule_is_deny_all_with_zero_allowed_hosts():
    env = _env(networking_config={"allowed_hosts": []})
    policy = build_network_policy(env)
    assert policy.rules[-1] == PolicyRule(domain="*", action="deny")


def test_final_rule_is_deny_all_with_one_allowed_host():
    env = _env(networking_config={"allowed_hosts": ["github.com"]})
    policy = build_network_policy(env)
    assert policy.rules[-1] == PolicyRule(domain="*", action="deny")


def test_final_rule_is_deny_all_with_many_allowed_hosts():
    env = _env(networking_config={"allowed_hosts": ["a.com", "b.com", "c.com", "d.com"]})
    policy = build_network_policy(env)
    assert policy.rules[-1] == PolicyRule(domain="*", action="deny")


def test_final_rule_action_is_deny_not_allow():
    env = _env(networking_config={"allowed_hosts": ["github.com"]})
    policy = build_network_policy(env)
    assert policy.rules[-1].action == "deny"


def test_final_rule_domain_is_star():
    env = _env(networking_config={"allowed_hosts": ["github.com"]})
    policy = build_network_policy(env)
    assert policy.rules[-1].domain == "*"


# ---------- allow rules carry action="allow" ----------


def test_allow_rules_have_action_allow():
    env = _env(networking_config={"allowed_hosts": ["github.com", "pypi.org"]})
    policy = build_network_policy(env)
    # All rules except the trailing deny are allows.
    for rule in policy.rules[:-1]:
        assert rule.action == "allow"


def test_allow_rules_carry_host_as_domain():
    env = _env(networking_config={"allowed_hosts": ["github.com", "pypi.org"]})
    policy = build_network_policy(env)
    assert [r.domain for r in policy.rules[:-1]] == ["github.com", "pypi.org"]


# ---------- wildcard hosts in allow list pass through unchanged ----------


def test_wildcard_allow_host_passes_through():
    env = _env(networking_config={"allowed_hosts": ["*.github.com"]})
    policy = build_network_policy(env)
    assert policy.rules == [
        PolicyRule(domain="*.github.com", action="allow"),
        PolicyRule(domain="*", action="deny"),
    ]


# ---------- rule count invariants ----------


def test_rule_count_equals_allowed_hosts_plus_one():
    env = _env(networking_config={"allowed_hosts": ["a", "b", "c"]})
    policy = build_network_policy(env)
    assert len(policy.rules) == 4
