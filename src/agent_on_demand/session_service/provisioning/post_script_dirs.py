"""Compute which Sprite directories need ``mkdir -p`` before the
post-provision-script ``fs.write`` round.

Extracted from `session_service/provisioning.py` so the runtime / skills
dispatch can be mutation-tested in isolation. The caller
(``_build_provision_script``) inlines the result into the provision shell
script as ``mkdir -p <dirs...>``.

Two reasons a directory needs pre-creation:

  - **MCP server config**: codex / gemini / opencode each write their
    config under a non-default home directory (``.codex/``,
    ``.gemini/``, ``.config/opencode/``). Without the ``mkdir`` the
    post-script ``fs.write`` fails and the session never reaches
    ``running``. Claude is excluded — its config goes to
    ``/home/sprite/.claude.json`` and the parent already exists.
  - **Inline skills**: each inline ``SkillSpec`` (one with explicit
    ``content``, not a github source) lands under
    ``<skills_root>/<name>/SKILL.md``, so the per-skill directory must
    exist first. Github-source skills install via ``npx skills add``,
    which makes its own directories — they don't need pre-creation here.

The runtime name → directory mapping is tied to each runtime's MCP
config writer (``runtimes/codex_config.py`` etc); a refactor that
renames a runtime or moves a config path must update both, and mutmut
catches a drift here that would silently leave a failed mkdir.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import SessionSpec


# Per-runtime base directory for the MCP config file. Claude writes to
# ``~/.claude.json`` directly; its parent (``/home/sprite``) is assumed to
# exist on the Sprite base image, so claude is intentionally absent.
_RUNTIME_MCP_CONFIG_DIR: dict[str, str] = {
    "codex": "/home/sprite/.codex",
    "gemini": "/home/sprite/.gemini",
    "opencode": "/home/sprite/.config/opencode",
}


def directories_for_post_script_writes(spec: SessionSpec) -> list[str]:
    """Return the list of directories to ``mkdir -p`` before the
    post-script ``fs.write`` round.

    Order matches the underlying writes: MCP config dir first (one
    entry, only present for runtimes in ``_RUNTIME_MCP_CONFIG_DIR``),
    then inline-skill dirs in the order the skills appear on the spec.
    """
    dirs: list[str] = []
    if spec.mcp_servers:
        mcp_dir = _RUNTIME_MCP_CONFIG_DIR.get(spec.runtime.name)
        if mcp_dir is not None:
            dirs.append(mcp_dir)
    if spec.runtime.skills_root:
        for s in spec.skills:
            if s.content is not None:
                # Inline skills always carry a name (the validator
                # enforces it). Use ``assert`` rather than a raise — we
                # treat a violated invariant as a programming error,
                # not user input.
                assert s.name is not None
                dirs.append(f"{spec.runtime.skills_root}/{s.name}")
    return dirs
