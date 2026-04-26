"""Render shell install commands for a single package manager.

Extracted from `session_service/provisioning.py` so the per-manager
dispatch — six known managers, raise on the seventh — can be
mutation-tested in isolation. The caller (``_build_provision_script``)
iterates ``PACKAGE_MANAGER_ORDER`` and feeds each manager's package list
through ``package_commands``; the result is inlined into the provision
shell script invoked under ``bash -l``.

Why this is worth a mutmut slot: the API validator (``VALID_PACKAGE_MANAGERS``
in ``views/environments.py``) blocks unknown managers at request time,
and the caller iterates ``PACKAGE_MANAGER_ORDER`` — so in practice only
the six branched managers ever land here. The ``raise`` on a seventh is
defense-in-depth: if a future agent adds a new manager to ``ORDER`` (or
to the API allowlist) without adding a builder branch here, the user's
packages would silently drop. Mutmut catches a refactor that swaps the
``raise`` for a silent ``return []``.
"""

from __future__ import annotations

import shlex


# Order packages install in. ``apt`` first because language-level managers
# (cargo / gem / go / npm / pip) often need apt-installed libraries (libpq,
# libssl, etc.) on PATH before they can build. Within that ordering we
# alphabetize for predictability — the existing test suite pins this.
PACKAGE_MANAGER_ORDER = ("apt", "cargo", "gem", "go", "npm", "pip")


def package_commands(manager: str, pkgs: list[str]) -> list[str]:
    """Shell commands for ``<manager>`` to install ``pkgs``, suitable for
    inlining into a ``bash -l``-invoked provision script.

    Apt batches into a single ``update && install`` call; pip / npm / gem
    likewise install in one call. cargo and go install one binary at a
    time because their per-binary failure surfaces are noisier when
    batched.

    Raises ``ValueError`` for any manager not in ``PACKAGE_MANAGER_ORDER``
    — the API validator and the caller's loop should both prevent this,
    but the loud failure means a future drift in either won't silently
    drop the user's packages.
    """
    quoted = " ".join(shlex.quote(p) for p in pkgs)
    if manager == "apt":
        return [f"apt-get update -qq && apt-get install -y {quoted}"]
    if manager == "pip":
        return [f"pip install {quoted}"]
    if manager == "npm":
        return [f"npm install --global {quoted}"]
    if manager == "cargo":
        return [f"cargo install {shlex.quote(p)}" for p in pkgs]
    if manager == "gem":
        return [f"gem install {quoted}"]
    if manager == "go":
        return [f"go install {shlex.quote(p)}" for p in pkgs]
    raise ValueError(f"Unsupported package manager: {manager!r}")
