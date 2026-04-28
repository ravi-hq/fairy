"""Validate the GitHub-repository resource shape on session-create requests.

Extracted from `views/sessions.py` so the URL regex, mount-path
canonicalization, and the per-RunRequest dedup/limit checks can be
mutation-tested in isolation. The view layer keeps the wiring; this
module is pure (string in, validated string out, raises ``ValueError``).

Four pieces:

  - ``validate_github_url``: must match
    ``https://github.com/<owner>/<repo>(.git)?``. Returns the URL with
    any ``.git`` suffix removed (canonical form).
  - ``validate_mount_path``: must be absolute and not be ``/`` or
    ``/home/sprite``. Sprite-root mounts would shadow the agent's
    working directory; absolute-only requirement keeps clones
    predictable.
  - ``resolved_mount_path``: derives the default mount path from the
    repo URL when the user didn't pick one.
  - ``validate_resources_count_and_dedup``: per-RunRequest list-level
    checks (max 10, no duplicate mount paths after resolution).

The URL validation is the kind of thing that can silently weaken in
a refactor — drop the anchors and a malicious URL like
``https://github.com/x/y@evil.com/z`` could land in the provision
shell script.
"""

from __future__ import annotations

import re


# ``https://github.com/<owner>/<repo>(.git)?`` — anchored at both ends.
# Underscore, dot, and hyphen allowed in owner / repo segments.
# ``re.ASCII`` is load-bearing: without it ``\w`` is Unicode-aware in
# Python 3, so a URL like ``https://github.com/ów/rëpo`` would pass
# (GitHub itself rejects such names, but the validator should match
# the documented ASCII-only intent rather than rely on the upstream).
GITHUB_URL_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+(\.git)?$", re.ASCII)

# Maximum number of GitHub repositories per session. Each one is a git
# clone in the provision script — large lists slow startup linearly.
MAX_RESOURCES_PER_SESSION = 10

# Mount paths that would shadow the Sprite's working directory and
# break the agent. ``/`` and ``/home/sprite`` are explicitly disallowed.
RESERVED_MOUNT_PATHS = frozenset({"/", "/home/sprite"})


def validate_github_url(url: str) -> str:
    """Return the URL canonicalized (``.git`` suffix removed). Raises
    ``ValueError`` if the URL doesn't match the expected GitHub shape.
    """
    if not GITHUB_URL_RE.match(url):
        raise ValueError("Must be a valid https://github.com/<owner>/<repo> URL")
    return url.removesuffix(".git")


def validate_mount_path(path: str | None) -> str | None:
    """Return ``path`` unchanged on success; raises ``ValueError`` for
    a relative path or one that lands at the Sprite root. ``None``
    means "use the default" — the caller derives that via
    ``resolved_mount_path``.
    """
    if path is None:
        return None
    if not path.startswith("/"):
        raise ValueError("mount_path must be an absolute path")
    if path in RESERVED_MOUNT_PATHS:
        raise ValueError("mount_path must not be the Sprite root")
    return path


def resolved_mount_path(url: str, mount_path: str | None) -> str:
    """Default mount path: ``/workspace/<repo-name>``, derived from the
    last path segment of ``url`` (post-strip-trailing-slash). Returns
    ``mount_path`` unchanged when set.
    """
    if mount_path:
        return mount_path
    repo_name = url.rstrip("/").split("/")[-1]
    return f"/workspace/{repo_name}"


def validate_resources_count_and_dedup(mount_paths: list[str]) -> None:
    """List-level invariants for the resources field on a RunRequest:

      - At most ``MAX_RESOURCES_PER_SESSION`` resources.
      - No duplicate mount paths (after default-resolution by the
        caller). Two clones into the same dir would race on the
        filesystem.

    Raises ``ValueError`` on the first violation. Caller passes the
    resolved mount paths; this module doesn't see the per-resource
    objects.
    """
    if len(mount_paths) > MAX_RESOURCES_PER_SESSION:
        raise ValueError(f"Maximum {MAX_RESOURCES_PER_SESSION} resources per session")
    if len(mount_paths) != len(set(mount_paths)):
        raise ValueError("Duplicate mount_path in resources")
