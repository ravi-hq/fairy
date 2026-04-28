"""Direct unit tests for `validate_skills` and the inline/github helpers.

Mutation-tested. Tests cover each branch of the per-shape validation,
the cross-cutting checks (size limits, dedup, name regex), and the
shape discriminator (presence-of-``type``).

Tests are sync, no Django fixtures, no parametrize — required so
hammett (mutmut's runner) can execute them.
"""

import pytest

from agent_on_demand.validation.skill_validation import (
    GITHUB_SOURCE_RE,
    MAX_SKILL_CONTENT_BYTES,
    MAX_SKILL_DESCRIPTION_LEN,
    MAX_SKILLS_PER_AGENT,
    SKILL_HEREDOC_DELIMITER,
    SKILL_NAME_RE,
    validate_skills,
)


# ---------- helpers ----------


def _inline(name="ok", description="d", content="body"):
    return {"name": name, "description": description, "content": content}


def _github(name="ok", description="d", source="owner/repo", type_="github"):
    return {"type": type_, "name": name, "description": description, "source": source}


# ---------- empty / size limits ----------


def test_empty_list_is_accepted():
    assert validate_skills([]) == []


def test_max_skills_limit_at_boundary_is_accepted():
    """Exactly MAX_SKILLS_PER_AGENT is fine; one more is not. Pin both
    sides of the boundary."""
    skills = [_inline(name=f"s{i}") for i in range(MAX_SKILLS_PER_AGENT)]
    assert validate_skills(skills) == skills


def test_max_skills_limit_plus_one_rejects():
    skills = [_inline(name=f"s{i}") for i in range(MAX_SKILLS_PER_AGENT + 1)]
    with pytest.raises(ValueError, match=f"Maximum {MAX_SKILLS_PER_AGENT}"):
        validate_skills(skills)


# ---------- entry shape ----------


def test_non_dict_entry_rejects():
    """A bare string or list at index i must be rejected before any
    field-level checks. Pinned because the API validator's pydantic
    layer might not catch this if the field is typed as ``list`` (no
    item-shape constraint)."""
    with pytest.raises(ValueError, match=r"skills\[0\] must be an object"):
        validate_skills(["not-a-dict"])


# ---------- inline shape: required fields ----------


def test_inline_missing_name_rejects():
    """Asserts the index ``[0]`` appears in the error — distinguishes a
    mutant that drops the ``i`` arg through to ``_validate_inline``,
    which would produce ``skills[None]...`` instead."""
    with pytest.raises(ValueError, match=r"skills\[0\] missing required field: name"):
        validate_skills([{"description": "d", "content": "c"}])


def test_inline_error_includes_correct_index_for_second_entry():
    """The error reports the index of the offending skill in the list,
    not always ``[0]``. Pin the index propagation through the loop."""
    valid = _inline(name="ok")
    bad = {"description": "d", "content": "c"}  # missing name
    with pytest.raises(ValueError, match=r"skills\[1\] missing required field"):
        validate_skills([valid, bad])


def test_inline_missing_description_rejects():
    with pytest.raises(ValueError, match="missing required field: description"):
        validate_skills([{"name": "ok", "content": "c"}])


def test_inline_missing_content_rejects():
    with pytest.raises(ValueError, match="missing required field: content"):
        validate_skills([{"name": "ok", "description": "d"}])


def test_inline_with_unknown_key_rejects():
    """Unknown keys on an inline skill mean the caller probably meant
    a github skill but forgot ``type`` (or has a typo). Reject loudly."""
    skill = _inline()
    skill["scripts"] = "x"
    with pytest.raises(ValueError, match="unknown keys"):
        validate_skills([skill])


def test_inline_non_string_field_rejects():
    """Each inline-skill field must be a string. A numeric value
    present must reject — without this branch a payload like
    ``description=42`` would crash later when the validator
    ``len()``-s it."""
    skill = _inline()
    skill["description"] = 42
    with pytest.raises(ValueError, match="description must be a string"):
        validate_skills([skill])


# ---------- inline shape: content limits ----------


def test_inline_content_at_limit_is_accepted():
    """Right at MAX_SKILL_CONTENT_BYTES is fine; one byte over is not."""
    body = "x" * MAX_SKILL_CONTENT_BYTES
    assert validate_skills([_inline(content=body)]) == [_inline(content=body)]


def test_inline_content_over_limit_rejects():
    body = "x" * (MAX_SKILL_CONTENT_BYTES + 1)
    with pytest.raises(ValueError, match=f"exceeds {MAX_SKILL_CONTENT_BYTES} bytes"):
        validate_skills([_inline(content=body)])


def test_inline_content_size_check_uses_utf8_bytes_not_char_count():
    """Pin the size check on byte count, not char count — a multibyte
    UTF-8 char takes 3-4 bytes and could push past the byte limit even
    though the visible char count is well under it. Distinguishes a
    mutant that drops the ``.encode("utf-8")`` and just uses ``len``."""
    # 🎯 is 4 bytes in UTF-8. (limit_bytes // 4) chars exactly fills it;
    # one more char pushes 4 bytes over.
    char_count = MAX_SKILL_CONTENT_BYTES // 4 + 1
    body = "🎯" * char_count
    # Visible char count is well under the byte limit.
    assert len(body) < MAX_SKILL_CONTENT_BYTES
    # But the byte size is over.
    assert len(body.encode("utf-8")) > MAX_SKILL_CONTENT_BYTES
    with pytest.raises(ValueError):
        validate_skills([_inline(content=body)])


def test_inline_content_with_heredoc_delimiter_rejects():
    """The heredoc delimiter would break the bash heredoc that materializes
    the content — reject the literal string."""
    body = f"some content {SKILL_HEREDOC_DELIMITER} sneaky"
    with pytest.raises(ValueError, match=SKILL_HEREDOC_DELIMITER):
        validate_skills([_inline(content=body)])


# ---------- github shape: required fields ----------


def test_github_missing_source_rejects():
    """Asserts the index ``[0]`` appears in the error — distinguishes a
    mutant that drops ``i`` through to ``_validate_github``, which
    would produce ``skills[None]...`` instead."""
    skill = _github()
    del skill["source"]
    with pytest.raises(ValueError, match=r"skills\[0\] missing required field: source"):
        validate_skills([skill])


def test_github_error_includes_correct_index_for_second_entry():
    """Same as the inline version: index propagation through the loop
    lands at the offending skill, not always ``[0]``."""
    valid = _github(name="ok")
    bad = _github(name="ok2")
    del bad["source"]
    with pytest.raises(ValueError, match=r"skills\[1\] missing required field"):
        validate_skills([valid, bad])


def test_github_missing_type_rejects():
    """Without ``type`` the skill is interpreted as inline — and inline
    requires ``content``, which is rejected by inline's unknown-key
    check (since ``source`` would be unknown to inline). The error
    message comes from the inline branch."""
    skill = _github()
    del skill["type"]
    with pytest.raises(ValueError, match="unknown keys"):
        validate_skills([skill])


def test_github_with_content_field_rejects_as_unknown_key():
    """Inline and github shapes are mutually exclusive — including
    ``content`` on a github skill means the caller mixed them up. The
    extra-keys check catches this."""
    skill = _github()
    skill["content"] = "body"
    with pytest.raises(ValueError, match="unknown keys"):
        validate_skills([skill])


def test_github_type_must_be_literal_github():
    """A typo'd type like ``"gitub"`` lands in the unknown-type branch
    rather than slipping through to a successful install."""
    skill = _github(type_="gitub")
    with pytest.raises(ValueError, match=r"\.type 'gitub' unsupported"):
        validate_skills([skill])


def test_github_source_must_match_owner_slash_repo():
    """Pin the source format so a refactor of ``GITHUB_SOURCE_RE`` is
    forced to be conscious."""
    skill = _github(source="not-a-valid-source")
    with pytest.raises(ValueError, match="must be 'owner/repo'"):
        validate_skills([skill])


def test_github_name_when_present_must_be_string():
    skill = _github()
    skill["name"] = 42
    with pytest.raises(ValueError, match="name must be a string"):
        validate_skills([skill])


def test_github_required_field_non_string_rejects_with_specific_message():
    """A non-string ``source`` on a github skill must reject with a
    typed message. Pinned because the message is the only observable
    difference between ``raise ValueError(...)`` and a mutant that
    swaps to ``raise ValueError(None)``."""
    skill = _github()
    skill["source"] = 42
    with pytest.raises(ValueError, match=r"skills\[0\]\.source must be a string"):
        validate_skills([skill])


def test_github_name_omitted_is_accepted():
    """``name`` is optional for github — omitting it means "install
    every SKILL.md from the repo"."""
    skill = _github()
    del skill["name"]
    assert validate_skills([skill]) == [skill]


# ---------- name regex ----------


def test_name_with_uppercase_rejects():
    with pytest.raises(ValueError, match="must match"):
        validate_skills([_inline(name="Bad-Name")])


def test_name_with_underscore_rejects():
    with pytest.raises(ValueError, match="must match"):
        validate_skills([_inline(name="bad_name")])


def test_name_starting_with_dash_rejects():
    """Anchor at the start: must begin with [a-z0-9], not ``-``."""
    with pytest.raises(ValueError, match="must match"):
        validate_skills([_inline(name="-leading-dash")])


def test_name_too_long_rejects():
    """64-char limit (1 char + up to 63 of [a-z0-9-])."""
    long_name = "a" * 65
    with pytest.raises(ValueError, match="must match"):
        validate_skills([_inline(name=long_name)])


def test_name_at_max_length_is_accepted():
    """Exactly 64 chars is fine; 65 isn't. Pin the boundary."""
    name = "a" * 64
    assert validate_skills([_inline(name=name)])[0]["name"] == name


# ---------- description size limit ----------


def test_description_at_limit_accepted():
    desc = "x" * MAX_SKILL_DESCRIPTION_LEN
    assert validate_skills([_inline(description=desc)])[0]["description"] == desc


def test_description_over_limit_rejects():
    desc = "x" * (MAX_SKILL_DESCRIPTION_LEN + 1)
    with pytest.raises(ValueError, match=f"exceeds {MAX_SKILL_DESCRIPTION_LEN} chars"):
        validate_skills([_inline(description=desc)])


# ---------- dedup ----------


def test_duplicate_inline_names_reject():
    with pytest.raises(ValueError, match=r"duplicate name 'shared'"):
        validate_skills([_inline(name="shared"), _inline(name="shared")])


def test_duplicate_github_names_reject():
    with pytest.raises(ValueError, match=r"duplicate name 'shared'"):
        validate_skills([_github(name="shared"), _github(name="shared", source="owner/other")])


def test_inline_and_github_with_same_name_collide():
    """Names dedup across shapes — if a user has both an inline and a
    github skill named ``shared``, only one can land."""
    inline = _inline(name="shared")
    github = _github(name="shared")
    with pytest.raises(ValueError, match=r"duplicate name 'shared'"):
        validate_skills([inline, github])


def test_two_whole_repo_github_entries_with_same_source_reject():
    """Whole-repo github installs (``name`` omitted) dedup on source.
    Two ``{type, description, source}`` entries with the same source
    are duplicates."""
    skill = _github()
    del skill["name"]
    with pytest.raises(ValueError, match="duplicate source"):
        validate_skills([skill, dict(skill)])


def test_named_and_whole_repo_github_can_coexist_for_same_source():
    """A specific-skill install and a whole-repo install of the same
    source aren't duplicates — they install different things."""
    named = _github(name="specific-skill")
    whole_repo = _github()
    del whole_repo["name"]
    # Should not raise.
    assert validate_skills([named, whole_repo]) == [named, whole_repo]


def test_two_whole_repo_github_entries_with_different_sources_accepted():
    """Two whole-repo github installs from *different* sources aren't
    duplicates — each pulls from its own repo. The dedup key for a
    whole-repo entry is the source, not a sentinel — pinning the
    distinction so a mutant that swaps the source-based key for a
    constant (e.g. ``None``) gets caught when it would otherwise
    collide on the constant."""
    first = _github(source="owner-a/repo")
    del first["name"]
    second = _github(source="owner-b/repo")
    del second["name"]
    # Should not raise; both must end up in the validated list.
    assert validate_skills([first, second]) == [first, second]


# ---------- exported regexes / constants (defensive sanity checks) ----------


def test_skill_name_re_rejects_empty_string():
    """``+0,63}`` allows 1-64 chars total (one initial + up to 63 trail)
    — empty must NOT match."""
    assert SKILL_NAME_RE.match("") is None


def test_github_source_re_requires_slash():
    """No slash → invalid."""
    assert GITHUB_SOURCE_RE.match("noslash") is None


def test_github_source_re_rejects_extra_segments():
    """``a/b/c`` should be invalid — only one slash."""
    assert GITHUB_SOURCE_RE.match("a/b/c") is None
