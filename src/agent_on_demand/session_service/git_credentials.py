"""Compose the body of `/tmp/.git-credentials`.

The file is consumed by git's `store` credential helper at clone /
fetch / push time. Each line follows the format
``https://<token>:x-oauth-basic@github.com`` — GitHub's documented
contract for using a personal access token as the password component
of HTTP basic auth (the username is the literal ``x-oauth-basic``
sentinel). A wrong format silently breaks git auth or — worse —
exposes tokens in error messages, so the line builder lives in its
own pure module direct-testable under mutmut's hammett runner without
pulling in the Sprite or ORM dependencies that
`write_git_credentials` carries.

Returns a ``list[str]`` rather than a joined string: line-joining and
trailing-newline handling are concerns of the caller writing to the
Sprite filesystem, not of the line builder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .specs import RepoSpec


def build_git_credentials_lines(repos: list["RepoSpec"]) -> list[str]:
    """Return one credential line per token-bearing repo.

    Repos with a falsy ``token`` (``None`` or ``""``) are skipped.
    Order is preserved from the input — no sort, no dedup. Each line
    is exactly ``https://<token>:x-oauth-basic@github.com``.
    """
    return [f"https://{r.token}:x-oauth-basic@github.com" for r in repos if r.token]
