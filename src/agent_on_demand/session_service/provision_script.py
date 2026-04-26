"""Render the combined provision shell script for a Sprite.

Extracted from `session_service/provisioning.py` so the script-building
logic — order-of-operations, per-stage line emission, the conditionals
around env-file chmod / git creds / packages / clones / user setup —
can be mutation-tested in isolation. The original
``_run_provision_setup`` keeps the file write + ``sprite.command`` call;
this module is pure (spec in, shell-script string out).

The provision flow is shaped around the WebSocket round-trip cost of
each ``sprite.command()`` (~5s of overhead regardless of payload). To
minimize round trips, all shell work — chmod, package installs, git
clones, user setup — is folded into a single bash script invoked with
one ``bash -l`` call. This module renders that script.

Stage order matters:

  1. ``set -e`` so any step failing aborts the rest.
  2. mkdir any parent dirs for files written *after* the script runs
     (MCP config for codex/gemini/opencode, inline-skill directories).
  3. chmod the pre-written ``/tmp`` files (env file always; git creds
     only if any repo had a token).
  4. Install packages — apt first because language-level managers
     (cargo/gem/go/npm/pip) often need apt-installed libs (libpq,
     libssl, etc.) on PATH before they can build.
  5. Git clones (with credential helper if any token is present).
  6. User-provided setup script, last — runs in an environment that
     has packages and repos in place.

A refactor that re-orders these silently breaks otherwise-valid
specs. Mutmut catches drift in the order, the conditionals, and the
shell-quoting.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from .package_commands import PACKAGE_MANAGER_ORDER, package_commands
from .post_script_dirs import directories_for_post_script_writes

if TYPE_CHECKING:
    from .specs import SessionSpec


# Paths inside the single-tenant Sprite VM, not the host — B108 doesn't apply.
ENV_FILE_PATH = "/tmp/aod-env"  # nosec B108
GIT_CREDS_PATH = "/tmp/.git-credentials"  # nosec B108
PROVISION_SCRIPT_PATH = "/tmp/aod-provision.sh"  # nosec B108


def build_provision_script(spec: SessionSpec) -> str:
    """Render the combined shell script invoked as the single
    ``sprite.command``. See module docstring for the stage order."""
    env = spec.environment
    lines: list[str] = ["#!/bin/bash", "set -e", ""]

    # mkdir for files that get fs.written AFTER the script runs (MCP config
    # for codex/gemini/opencode, every inline-skill dir). /home/sprite already
    # exists, so Claude's .claude.json and the default skills root don't need
    # to be created here.
    dirs_to_make = directories_for_post_script_writes(spec)
    if dirs_to_make:
        quoted = " ".join(shlex.quote(d) for d in dirs_to_make)
        lines.append(f"mkdir -p {quoted}")
        lines.append("")

    # chmod files pre-written to /tmp. The env file always exists at this
    # point; git creds only if any repo had a token.
    lines.append(f"chmod 600 {shlex.quote(ENV_FILE_PATH)}")
    if any(r.token for r in spec.repos):
        lines.append(f"chmod 600 {shlex.quote(GIT_CREDS_PATH)}")
    lines.append("")

    # Packages — iterate the canonical order so apt always runs first.
    if env and env.packages:
        for manager in PACKAGE_MANAGER_ORDER:
            pkgs = env.packages.get(manager, [])
            if not pkgs:
                continue
            for cmd in package_commands(manager, pkgs):
                lines.append(cmd)
        lines.append("")

    # Git clones, with credential helper config when any repo has a token.
    if spec.repos:
        if any(r.token for r in spec.repos):
            lines.append(
                f"git config --global credential.helper "
                f"{shlex.quote(f'store --file={GIT_CREDS_PATH}')}"
            )
        for repo in spec.repos:
            lines.append(
                f"git clone --depth=1 --quiet "
                f"{shlex.quote(repo.url)} {shlex.quote(repo.mount_path)}"
            )
        lines.append("")

    # User-provided setup script, last (packages and repos are in place).
    if env is not None:
        user_script = (env.setup_script or "").strip()
        if user_script:
            lines.append(user_script)
            lines.append("")

    return "\n".join(lines)
