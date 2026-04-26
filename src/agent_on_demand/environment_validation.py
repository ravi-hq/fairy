"""Validate the user-supplied fields on Environment create/update requests.

Extracted from `views/environments.py` so the three validators
(packages / env_vars / networking) can be mutation-tested in isolation
and shared between ``CreateEnvironmentRequest`` and
``UpdateEnvironmentRequest`` without duplicating the logic. The view
layer keeps the wiring; this module is pure (dict in, validated dict
out, raises ``ValueError``).

Three validators, each pinning a specific failure mode:

  - **packages**: manager allowlist. The list[str] shape is enforced
    by pydantic's annotation; this layer just gates which managers
    are recognized. Drift between this allowlist and
    ``PACKAGE_MANAGER_ORDER`` in package_commands.py would silently
    drop packages.
  - **env_vars**: POSIX shell variable name regex. Keys with
    hyphens / leading digits / dots would corrupt the
    ``KEY=value`` lines the provision script writes to
    ``/tmp/aod-env``.
  - **networking**: type allowlist (``unrestricted`` /
    ``limited``) plus the ``allowed_hosts`` shape requirement when
    type is ``limited``. A non-list ``allowed_hosts`` would silently
    bypass the firewall config.
"""

from __future__ import annotations

import re


VALID_PACKAGE_MANAGERS = frozenset({"apt", "cargo", "gem", "go", "npm", "pip"})

VALID_NETWORKING_TYPES = frozenset({"unrestricted", "limited"})

# Valid POSIX shell variable names. Keys that don't match this would
# corrupt /tmp/aod-env when written as ``KEY=value`` shell assignments —
# e.g. ``BAD-KEY`` would parse as ``BAD`` minus the value of ``KEY``.
ENV_VAR_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_packages(v: dict) -> dict:
    """Each key must be a recognized package manager. Returns ``v``
    unchanged on success; raises ``ValueError`` on the first unknown
    manager.

    The list[str] shape check is enforced by pydantic's
    ``dict[str, list[str]]`` annotation upstream; we only need to
    validate the manager name here.
    """
    for manager in v:
        if manager not in VALID_PACKAGE_MANAGERS:
            raise ValueError(
                f"Unknown package manager: {manager}. "
                f"Must be one of: {sorted(VALID_PACKAGE_MANAGERS)}"
            )
    return v


def validate_env_vars(v: dict) -> dict:
    """Each key must match the POSIX shell variable name regex.
    Returns ``v`` unchanged on success; raises ``ValueError`` on the
    first invalid key.
    """
    for key in v:
        if not ENV_VAR_KEY_RE.match(key):
            raise ValueError(f"Invalid env_var key {key!r}: must match [A-Za-z_][A-Za-z0-9_]*")
    return v


def validate_networking(v: dict) -> dict:
    """The ``networking`` field is a small dict — ``type`` must be in
    the allowlist, and when type is ``"limited"`` the optional
    ``allowed_hosts`` field must be a list (else the firewall config
    silently degrades to "no restrictions").

    Default ``type`` is ``"unrestricted"`` if absent (matches what the
    Create request uses for its default factory).
    """
    net_type = v.get("type", "unrestricted")
    if net_type not in VALID_NETWORKING_TYPES:
        raise ValueError("networking.type must be 'unrestricted' or 'limited'")
    if net_type == "limited":
        hosts = v.get("allowed_hosts", [])
        if not isinstance(hosts, list):
            raise ValueError("networking.allowed_hosts must be a list")
    return v
