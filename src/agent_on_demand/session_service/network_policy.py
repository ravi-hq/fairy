"""Build the `NetworkPolicy` for a session.

The deny-all rule must be appended *last* so allow-listed hosts take
precedence — a reorder would silently let everything through. The pure
construction lives here so it can be direct-tested under mutmut's
hammett runner without pulling in the Sprite SDK or ORM dependencies
that `_apply_network_policy` carries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sprites import NetworkPolicy, PolicyRule

if TYPE_CHECKING:
    from agent_on_demand.models import Environment


def build_network_policy(env: "Environment | None") -> NetworkPolicy | None:
    """Build the NetworkPolicy for a session, or None if no policy applies.

    Returns None when env is None or networking_type != 'limited' (the
    only restricted mode today). When 'limited', emits one allow rule per
    `networking_config.allowed_hosts`, then a final `*` deny rule.
    """
    if env is None or env.networking_type != "limited":
        return None
    allowed_hosts = (env.networking_config or {}).get("allowed_hosts", [])
    rules = [PolicyRule(domain=host, action="allow") for host in allowed_hosts]
    rules.append(PolicyRule(domain="*", action="deny"))
    return NetworkPolicy(rules=rules)
