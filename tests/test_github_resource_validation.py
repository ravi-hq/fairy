"""Direct unit tests for the github_resource_validation module.

Mutation-tested. Four functions plus the constants. Each test isolates
one mutation-killable branch.

Tests are sync, no Django fixtures, no parametrize — required so
hammett (mutmut's runner) can execute them.
"""

import pytest

from agent_on_demand.validation.github_resource_validation import (
    GITHUB_URL_RE,
    MAX_RESOURCES_PER_SESSION,
    RESERVED_MOUNT_PATHS,
    resolved_mount_path,
    validate_github_url,
    validate_mount_path,
    validate_resources_count_and_dedup,
)


# ---------- validate_github_url ----------


def test_github_url_canonical_form_accepted():
    assert validate_github_url("https://github.com/owner/repo") == "https://github.com/owner/repo"


def test_github_url_with_dot_git_suffix_is_stripped():
    """``.git`` suffix is canonicalized away — a refactor that drops
    the ``removesuffix`` call would leak the suffix into downstream
    git-clone commands."""
    assert (
        validate_github_url("https://github.com/owner/repo.git") == "https://github.com/owner/repo"
    )


def test_github_url_with_dots_in_repo_name_accepted():
    """The character class allows dots in owner/repo — many real
    repositories have ``foo.bar.baz`` names."""
    assert (
        validate_github_url("https://github.com/owner/some.repo.name")
        == "https://github.com/owner/some.repo.name"
    )


def test_github_url_with_hyphens_and_underscores_accepted():
    assert validate_github_url("https://github.com/my-org/my_repo")


def test_github_url_http_rejects():
    """HTTP only — pin the scheme requirement so plain ``http://``
    URLs don't slip through."""
    with pytest.raises(ValueError, match="Must be a valid"):
        validate_github_url("http://github.com/owner/repo")


def test_github_url_wrong_host_rejects():
    """Only github.com — gitlab/bitbucket/etc don't match."""
    with pytest.raises(ValueError):
        validate_github_url("https://gitlab.com/owner/repo")


def test_github_url_with_path_traversal_rejects():
    """Anchored regex — extra path segments don't match."""
    with pytest.raises(ValueError):
        validate_github_url("https://github.com/owner/repo/extra")


def test_github_url_missing_repo_segment_rejects():
    with pytest.raises(ValueError):
        validate_github_url("https://github.com/owner")


def test_github_url_with_trailing_slash_rejects():
    """The regex doesn't accept trailing slash. Pin so a refactor that
    softens the ending anchor is caught."""
    with pytest.raises(ValueError):
        validate_github_url("https://github.com/owner/repo/")


def test_github_url_with_query_string_rejects():
    """No query strings — anchored regex rejects them."""
    with pytest.raises(ValueError):
        validate_github_url("https://github.com/owner/repo?branch=main")


def test_github_url_subdomain_rejects():
    """Only the exact github.com host — subdomains rejected."""
    with pytest.raises(ValueError):
        validate_github_url("https://api.github.com/repos/owner/repo")


def test_github_url_unicode_segments_rejects():
    """``re.ASCII`` is set on the regex so ``\\w`` only matches ASCII
    word characters. Pinned because dropping the flag would let URLs
    like ``https://github.com/ów/rëpo`` pass — GitHub itself rejects
    such names, but the validator should match the documented
    ASCII-only intent rather than rely on the upstream API."""
    with pytest.raises(ValueError):
        validate_github_url("https://github.com/ów/rëpo")


def test_github_url_error_exact_match():
    """Exact-match assertion — kills wrapper-style mutants on the
    error literal."""
    with pytest.raises(ValueError) as exc:
        validate_github_url("invalid")
    assert str(exc.value) == "Must be a valid https://github.com/<owner>/<repo> URL"


# ---------- validate_mount_path ----------


def test_mount_path_none_returns_none():
    """``None`` means "use the default" — caller derives via
    ``resolved_mount_path``. Pinned because the early-return for None
    is what makes ``mount_path: str | None`` semantics work."""
    assert validate_mount_path(None) is None


def test_absolute_mount_path_accepted():
    assert validate_mount_path("/workspace/project") == "/workspace/project"


def test_relative_mount_path_rejects():
    """Relative paths are ambiguous — could land anywhere depending on
    cwd. Pinned with exact-match on the error literal."""
    with pytest.raises(ValueError) as exc:
        validate_mount_path("workspace/project")
    assert str(exc.value) == "mount_path must be an absolute path"


def test_root_mount_path_rejects():
    """Mounting at ``/`` would shadow the entire Sprite filesystem."""
    with pytest.raises(ValueError) as exc:
        validate_mount_path("/")
    assert str(exc.value) == "mount_path must not be the Sprite root"


def test_home_sprite_mount_path_rejects():
    """``/home/sprite`` is the agent's working directory; mounting
    there would shadow the agent itself."""
    with pytest.raises(ValueError) as exc:
        validate_mount_path("/home/sprite")
    assert str(exc.value) == "mount_path must not be the Sprite root"


def test_path_under_home_sprite_accepted():
    """``/home/sprite/something`` is fine — only the exact root
    paths are reserved. Pin so a refactor that uses ``startswith``
    instead of equality is caught."""
    assert validate_mount_path("/home/sprite/project") == "/home/sprite/project"


def test_empty_string_mount_path_rejects():
    """Empty string is not absolute (doesn't start with ``/``)."""
    with pytest.raises(ValueError, match="must be an absolute path"):
        validate_mount_path("")


# ---------- resolved_mount_path ----------


def test_resolved_explicit_mount_path_returned_as_is():
    assert resolved_mount_path("https://github.com/x/y", "/custom") == "/custom"


def test_resolved_default_uses_workspace_plus_repo_name():
    assert resolved_mount_path("https://github.com/owner/myrepo", None) == "/workspace/myrepo"


def test_resolved_default_strips_trailing_slash():
    """``rstrip("/")`` on the URL handles trailing-slash sloppiness
    in the input."""
    assert resolved_mount_path("https://github.com/owner/myrepo/", None) == "/workspace/myrepo"


def test_resolved_default_only_strips_slashes_not_other_chars():
    """``rstrip`` strips any of the chars in its argument — passing
    ``"/"`` strips only ``/``. Distinguishes a mutant that wraps the
    arg in extra characters (e.g. ``rstrip("XX/XX")`` would also
    strip ``X`` from the end of the URL and produce the wrong
    repo name)."""
    # Repo name ends in X — must NOT be stripped.
    assert resolved_mount_path("https://github.com/owner/repoX", None) == "/workspace/repoX"


# ---------- validate_resources_count_and_dedup ----------


def test_count_at_max_is_accepted():
    """Exactly MAX_RESOURCES_PER_SESSION is fine; one more is not."""
    paths = [f"/p{i}" for i in range(MAX_RESOURCES_PER_SESSION)]
    # No raise.
    validate_resources_count_and_dedup(paths)


def test_count_over_max_rejects():
    paths = [f"/p{i}" for i in range(MAX_RESOURCES_PER_SESSION + 1)]
    with pytest.raises(ValueError, match=f"Maximum {MAX_RESOURCES_PER_SESSION}"):
        validate_resources_count_and_dedup(paths)


def test_no_duplicate_paths_accepted():
    validate_resources_count_and_dedup(["/a", "/b", "/c"])


def test_duplicate_paths_reject():
    """Exact-match assertion — kills wrapper-style mutants on the
    error literal."""
    with pytest.raises(ValueError) as exc:
        validate_resources_count_and_dedup(["/a", "/b", "/a"])
    assert str(exc.value) == "Duplicate mount_path in resources"


def test_count_check_takes_priority_over_dedup_check():
    """Both checks fail — count is reported first. Distinguishes a
    refactor that swaps the order."""
    # Same path repeated > MAX times — both checks would fail.
    paths = [f"/p{i % 3}" for i in range(MAX_RESOURCES_PER_SESSION + 1)]
    with pytest.raises(ValueError, match=f"Maximum {MAX_RESOURCES_PER_SESSION}"):
        validate_resources_count_and_dedup(paths)


# ---------- exported constants ----------


def test_max_resources_constant_value():
    """If this changes, plenty of operator-facing docs need to update too."""
    assert MAX_RESOURCES_PER_SESSION == 10


def test_reserved_mount_paths_contents():
    assert RESERVED_MOUNT_PATHS == frozenset({"/", "/home/sprite"})


def test_reserved_mount_paths_is_frozenset():
    assert isinstance(RESERVED_MOUNT_PATHS, frozenset)


def test_github_url_re_is_anchored_at_both_ends():
    """A non-anchored regex would let prefix/suffix attacks slip
    through — pin the anchoring."""
    # Substring would otherwise match this; anchored regex doesn't.
    assert GITHUB_URL_RE.match("evil.com https://github.com/x/y") is None
    assert GITHUB_URL_RE.match("https://github.com/x/y suffix") is None
