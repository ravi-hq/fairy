"""Direct unit tests for `build_git_credentials_lines`.

Mutation-tested. Each test pins one mutation-killable property of the
`/tmp/.git-credentials` body that GitHub's `store` credential helper
consumes:

  - Empty input → empty output.
  - All-falsy tokens (``None`` or ``""``) → empty output.
  - Mixed input → only token-bearing repos appear, in input order.
  - Each emitted line is exactly
    ``https://<token>:x-oauth-basic@github.com`` — character-for-
    character. The ``:x-oauth-basic@github.com`` suffix is GitHub's
    PAT-as-password contract; mutating any character silently breaks
    git auth on the Sprite.
  - Tokens are interpolated VERBATIM; no URL-encoding, no
    transformation, no dedup, no sort.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them. ``RepoSpec`` is duck-typed via
``SimpleNamespace``.
"""

from types import SimpleNamespace

from agent_on_demand.session_service.git_credentials import build_git_credentials_lines


def _repo(token):
    return SimpleNamespace(token=token)


# ---------- empty / all-falsy → empty ----------


def test_empty_repo_list_returns_empty():
    assert build_git_credentials_lines([]) == []


def test_all_none_tokens_returns_empty():
    assert build_git_credentials_lines([_repo(None), _repo(None)]) == []


def test_all_empty_string_tokens_returns_empty():
    # Empty-string tokens are falsy and skipped — same as None.
    assert build_git_credentials_lines([_repo(""), _repo("")]) == []


def test_mixed_none_and_empty_string_returns_empty():
    assert build_git_credentials_lines([_repo(None), _repo("")]) == []


# ---------- exact line format ----------


def test_single_repo_line_is_exact_full_string():
    """Full-string equality on the single emitted line. The
    ``:x-oauth-basic@github.com`` suffix is GitHub's documented
    contract for PAT-as-password basic auth — any mutant that drops a
    character (the colon, the literal ``x-oauth-basic``, the ``@``,
    the ``github.com`` host) breaks auth on the Sprite."""
    lines = build_git_credentials_lines([_repo("ghp_abc123")])
    assert lines == ["https://ghp_abc123:x-oauth-basic@github.com"]


def test_token_interpolated_verbatim():
    """The token is inlined exactly as supplied — no URL-encoding,
    no escaping, no transformation. Pin so a mutant that wraps
    ``r.token`` in ``str(...)`` or applies ``.strip()`` is caught."""
    lines = build_git_credentials_lines([_repo("abc-123")])
    assert lines == ["https://abc-123:x-oauth-basic@github.com"]


def test_line_starts_with_https_scheme():
    """Pin the ``https://`` scheme prefix — git's `store` helper
    matches credentials by scheme+host, so a mutant that swaps to
    ``http://`` or drops the scheme silently sends no credentials."""
    lines = build_git_credentials_lines([_repo("t")])
    assert lines == ["https://t:x-oauth-basic@github.com"]


def test_line_uses_x_oauth_basic_username_literal():
    """The literal ``x-oauth-basic`` is GitHub's required username
    sentinel when using a PAT as the password. A mutant that swaps it
    for any other string (even ``oauth2`` or empty) breaks auth."""
    lines = build_git_credentials_lines([_repo("tok")])
    assert lines[0] == "https://tok:x-oauth-basic@github.com"


def test_line_targets_github_com_host():
    """Pin ``@github.com`` — credentials stored against another host
    won't match GitHub clones."""
    lines = build_git_credentials_lines([_repo("tok")])
    assert lines[0] == "https://tok:x-oauth-basic@github.com"


# ---------- ordering / mixing ----------


def test_mixed_tokens_only_truthy_appear_in_input_order():
    """Repos with falsy tokens are skipped; the remainder appear in
    input order (no sort, no dedup). Distinguishes a mutant that
    reverses the comprehension or sorts the result."""
    repos = [
        _repo("first"),
        _repo(None),
        _repo("second"),
        _repo(""),
        _repo("third"),
    ]
    assert build_git_credentials_lines(repos) == [
        "https://first:x-oauth-basic@github.com",
        "https://second:x-oauth-basic@github.com",
        "https://third:x-oauth-basic@github.com",
    ]


def test_two_distinct_tokens_both_emitted_in_order():
    repos = [_repo("alpha"), _repo("beta")]
    assert build_git_credentials_lines(repos) == [
        "https://alpha:x-oauth-basic@github.com",
        "https://beta:x-oauth-basic@github.com",
    ]


def test_duplicate_tokens_are_not_deduped():
    """Caller-determined input order is preserved verbatim — if the
    caller hands in the same token twice, both lines appear. Pin so a
    mutant that converts via ``set(...)`` is caught."""
    repos = [_repo("same"), _repo("same")]
    assert build_git_credentials_lines(repos) == [
        "https://same:x-oauth-basic@github.com",
        "https://same:x-oauth-basic@github.com",
    ]


def test_input_order_is_not_sorted():
    """Reverse-alphabetical input stays reverse-alphabetical out —
    no sort applied. Distinguishes a mutant that wraps the
    comprehension in ``sorted(...)``."""
    repos = [_repo("zzz"), _repo("aaa")]
    assert build_git_credentials_lines(repos) == [
        "https://zzz:x-oauth-basic@github.com",
        "https://aaa:x-oauth-basic@github.com",
    ]


def test_first_repo_with_none_does_not_shift_order():
    """A leading falsy-token repo is skipped without consuming a
    slot — the next truthy repo is line[0]."""
    repos = [_repo(None), _repo("only")]
    assert build_git_credentials_lines(repos) == [
        "https://only:x-oauth-basic@github.com",
    ]


# ---------- count invariants ----------


def test_line_count_equals_truthy_token_count():
    repos = [_repo("a"), _repo(None), _repo("b"), _repo(""), _repo("c")]
    assert len(build_git_credentials_lines(repos)) == 3


def test_three_truthy_repos_emit_three_lines():
    repos = [_repo("a"), _repo("b"), _repo("c")]
    assert len(build_git_credentials_lines(repos)) == 3
