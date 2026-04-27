"""Build the `skills.sh` install command for a github-source skill.

The composed string is handed to ``bash -lc`` on the Sprite, so every
user-controlled component (``source``, ``agent_id``, ``name``) MUST be
``shlex.quote``-d before interpolation — ``source`` is an
``owner/repo`` identifier supplied by API callers and would otherwise
allow arbitrary command injection (e.g. ``foo; rm -rf /``). The
quoting guard is load-bearing for security, so the builder lives in
its own pure module direct-testable under mutmut's hammett runner.

The function returns a single command string ready for
``sprite.command("bash", "-lc", cmd).run()``; callers retain
responsibility for invoking the Sprite SDK.
"""

from __future__ import annotations

import shlex


def build_skills_install_command(source: str, agent_id: str, name: str | None) -> str:
    """Return the `npx skills@latest add ...` command for a github skill.

    When ``name`` is truthy, ``--skill <name>`` is appended so the CLI
    installs a single SKILL.md from the repo. When falsy (``None`` or
    ``""``), the flag is omitted and the CLI installs every SKILL.md
    in the repo.
    """
    cmd = (
        f"npx -y skills@latest add {shlex.quote(source)} "
        f"--global --agent {shlex.quote(agent_id)} --yes"
    )
    if name:
        cmd += f" --skill {shlex.quote(name)}"
    return cmd
