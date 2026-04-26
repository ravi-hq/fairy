"""Validate the ``skills`` field on agent create/update requests.

Extracted from `views/agents.py` so the validation surface — two skill
shapes (inline / github), per-shape required-key checks, the
content-size limit, the heredoc-delimiter blocklist, the name regex,
and the dedup logic — can be mutation-tested in isolation. The view
layer keeps the wiring (request parsing, error mapping to 422); this
module is pure (dict-list in, validated dict-list out, raises
``ValueError`` on bad input).

Two skill shapes:

  - **inline**: ``{"name": ..., "description": ..., "content": ...}``.
    Content is shipped in-band and gets written to
    ``<skills_root>/<name>/SKILL.md`` at provision time.
  - **github**: ``{"type": "github", "description": ..., "source": ...,
    "name": ...?}``. Installed on the Sprite at provision time via the
    skills.sh CLI (``npx skills add ...``). ``name`` selects a single
    skill from the repo via the CLI's ``--skill`` flag; omit it to
    install every ``SKILL.md`` the repo exposes.

Detection: presence of the ``type`` field. The inline shape omits it.
"""

from __future__ import annotations

import re


MAX_SKILLS_PER_AGENT = 20
MAX_SKILL_DESCRIPTION_LEN = 1024
MAX_SKILL_CONTENT_BYTES = 64 * 1024

# Skill names form filesystem paths and shell argv elements; restrict the
# character set accordingly. Anchored at both ends — the name must match
# the whole string, not a substring.
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# ``owner/repo`` — same character class GitHub allows for both segments.
GITHUB_SOURCE_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

INLINE_SKILL_KEYS = frozenset({"name", "description", "content"})
GITHUB_SKILL_KEYS = frozenset({"type", "name", "description", "source"})
GITHUB_REQUIRED_KEYS = frozenset({"type", "description", "source"})

# Inline skill content is materialized via a bash heredoc at provision
# time; this delimiter must not appear in the body or it would terminate
# the heredoc early and leak the rest of the script into the file.
SKILL_HEREDOC_DELIMITER = "SKILL_EOF"


def _validate_inline(skill: dict, i: int) -> None:
    extra = set(skill) - INLINE_SKILL_KEYS
    if extra:
        raise ValueError(
            f"skills[{i}]: unknown keys {sorted(extra)!r}. "
            f"Allowed for inline skills: {sorted(INLINE_SKILL_KEYS)}"
        )
    for field_name in ("name", "description", "content"):
        if field_name not in skill:
            raise ValueError(f"skills[{i}] missing required field: {field_name}")
        if not isinstance(skill[field_name], str):
            raise ValueError(f"skills[{i}].{field_name} must be a string")
    content = skill["content"]
    if len(content.encode("utf-8")) > MAX_SKILL_CONTENT_BYTES:
        raise ValueError(f"skills[{i}].content exceeds {MAX_SKILL_CONTENT_BYTES} bytes")
    if SKILL_HEREDOC_DELIMITER in content:
        raise ValueError(f"skills[{i}].content must not contain {SKILL_HEREDOC_DELIMITER!r}")


def _validate_github(skill: dict, i: int) -> None:
    extra = set(skill) - GITHUB_SKILL_KEYS
    if extra:
        raise ValueError(
            f"skills[{i}]: unknown keys {sorted(extra)!r}. "
            f"Allowed for github skills: {sorted(GITHUB_SKILL_KEYS)}"
        )
    for field_name in GITHUB_REQUIRED_KEYS:
        if field_name not in skill:
            raise ValueError(f"skills[{i}] missing required field: {field_name}")
        if not isinstance(skill[field_name], str):
            raise ValueError(f"skills[{i}].{field_name} must be a string")
    # ``name`` is optional for github; if present, it must still be a string.
    if "name" in skill and not isinstance(skill["name"], str):
        raise ValueError(f"skills[{i}].name must be a string")
    if skill["type"] != "github":
        raise ValueError(f"skills[{i}].type {skill['type']!r} unsupported (only 'github')")
    if not GITHUB_SOURCE_RE.match(skill["source"]):
        raise ValueError(f"skills[{i}].source {skill['source']!r} must be 'owner/repo'")


def validate_skills(skills: list) -> list:
    """Validate every skill in ``skills`` and return it unchanged on
    success. Raises ``ValueError`` on the first violation; the caller
    in views/agents.py turns that into a 422 with the message verbatim.
    """
    if len(skills) > MAX_SKILLS_PER_AGENT:
        raise ValueError(f"Maximum {MAX_SKILLS_PER_AGENT} skills per agent")
    seen_dedup_keys: set[str] = set()
    for i, skill in enumerate(skills):
        if not isinstance(skill, dict):
            raise ValueError(f"skills[{i}] must be an object")

        # Discriminate by presence of ``type``. Inline skills omit it.
        is_github = "type" in skill
        if is_github:
            _validate_github(skill, i)
        else:
            _validate_inline(skill, i)

        # Name regex applies whenever ``name`` is present. Github skills may
        # omit it (meaning: install every SKILL.md from the repo); inline
        # validation above already required it.
        if "name" in skill:
            name = skill["name"]
            if not SKILL_NAME_RE.match(name):
                raise ValueError(f"skills[{i}].name {name!r} must match [a-z0-9][a-z0-9-]{{0,63}}")
            dedup_key = name
        else:
            # Whole-repo github install. Dedup on source so two "all skills
            # from owner/repo" entries collide, but a per-skill entry from
            # the same source can coexist (different dedup key).
            dedup_key = f"@github:{skill['source']}"

        if dedup_key in seen_dedup_keys:
            label = "name" if "name" in skill else "source"
            raise ValueError(f"skills[{i}]: duplicate {label} {dedup_key!r}")
        seen_dedup_keys.add(dedup_key)

        if len(skill["description"]) > MAX_SKILL_DESCRIPTION_LEN:
            raise ValueError(f"skills[{i}].description exceeds {MAX_SKILL_DESCRIPTION_LEN} chars")
    return skills
