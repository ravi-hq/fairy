"""Tests for scripts/auto_revert.py.

Covers the decision logic and PR-body shape. Network and shell calls are
mocked — the script's only side effects are HTTP to Render and shell-out
to git/gh, both of which we don't want hitting real systems in CI.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from scripts.auto_revert import (
    FAILED_STATUSES,
    MIGRATIONS_PREFIX,
    NEEDS_HUMAN_LABEL,
    PR_TITLE_PREFIX,
    FailedDeploy,
    commit_touches_migrations,
    existing_revert_pr_exists,
    fetch_failed_deploys,
    is_revert_commit,
    process_deploy,
    render_pr_body,
)


# === fetch_failed_deploys ===


def _deploy_entry(*, status: str, sha: str | None = "abc123def456", deploy_id: str = "dep-1"):
    """Build a Render API deploy entry. Wrapped under "deploy" like the real API."""
    return {
        "deploy": {
            "id": deploy_id,
            "status": status,
            "commit": {"id": sha} if sha else None,
            "finishedAt": "2026-04-26T20:00:00Z",
            "createdAt": "2026-04-26T19:55:00Z",
        },
        "cursor": "opaque-cursor",
    }


def test_fetch_failed_deploys_filters_to_failed_statuses(mocker):
    api_response = [
        _deploy_entry(status="live", deploy_id="dep-live"),
        _deploy_entry(status="build_failed", deploy_id="dep-bf"),
        _deploy_entry(status="update_failed", deploy_id="dep-uf"),
        _deploy_entry(status="canceled", deploy_id="dep-cancel"),
        _deploy_entry(status="created", deploy_id="dep-created"),
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = api_response
    mocker.patch("scripts.auto_revert.requests.get", return_value=mock_resp)

    out = fetch_failed_deploys("srv-x", "tok")

    assert {d.deploy_id for d in out} == {"dep-bf", "dep-uf", "dep-cancel"}
    assert all(d.status in FAILED_STATUSES for d in out)


def test_fetch_failed_deploys_skips_entries_without_commit_sha(mocker):
    mock_resp = MagicMock()
    mock_resp.json.return_value = [
        _deploy_entry(status="build_failed", sha=None, deploy_id="dep-no-sha"),
        _deploy_entry(status="build_failed", sha="abcdef123456", deploy_id="dep-with-sha"),
    ]
    mocker.patch("scripts.auto_revert.requests.get", return_value=mock_resp)

    out = fetch_failed_deploys("srv-x", "tok")

    assert [d.deploy_id for d in out] == ["dep-with-sha"]


def test_fetch_failed_deploys_sends_bearer_auth_and_limit(mocker):
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    get = mocker.patch("scripts.auto_revert.requests.get", return_value=mock_resp)

    fetch_failed_deploys("srv-X", "secret-token", limit=5)

    args, kwargs = get.call_args
    assert args[0].endswith("/services/srv-X/deploys")
    assert kwargs["params"] == {"limit": 5}
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"


# === is_revert_commit ===


@pytest.mark.parametrize(
    "subject,expected",
    [
        ('Revert "Add session quota"', True),
        ("Revert auth.py rename", True),
        ("revert auth.py rename", False),  # case-sensitive
        ("Add session quota", False),
        ("Reverted typo in README", False),  # different prefix
    ],
)
def test_is_revert_commit_recognises_revert_subjects(mocker, subject, expected):
    mock_run = mocker.patch("scripts.auto_revert.subprocess.run")
    mock_run.return_value = MagicMock(stdout=subject + "\n")

    assert is_revert_commit("abc") is expected


# === commit_touches_migrations ===


def test_commit_touches_migrations_true_when_migration_changed(mocker):
    mock_run = mocker.patch("scripts.auto_revert.subprocess.run")
    mock_run.return_value = MagicMock(
        stdout=(
            "src/agent_on_demand/views/agents.py\n"
            "src/agent_on_demand/migrations/0017_new_field.py\n"
            "tests/test_agents.py\n"
        )
    )

    assert commit_touches_migrations("abc") is True


def test_commit_touches_migrations_false_for_code_only_change(mocker):
    mock_run = mocker.patch("scripts.auto_revert.subprocess.run")
    mock_run.return_value = MagicMock(
        stdout=("src/agent_on_demand/views/agents.py\ntests/test_agents.py\n")
    )

    assert commit_touches_migrations("abc") is False


def test_migrations_prefix_matches_claude_md_danger_zone():
    """Sanity check: the prefix must match the project's actual migration dir.

    If someone moves migrations, this test fails loudly so they update both.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    assert (repo_root / MIGRATIONS_PREFIX).is_dir(), (
        f"{MIGRATIONS_PREFIX} no longer exists; update auto_revert.py"
    )


# === existing_revert_pr_exists ===


def test_existing_revert_pr_exists_true_when_gh_returns_match(mocker):
    mock_run = mocker.patch("scripts.auto_revert.subprocess.run")
    mock_run.return_value = MagicMock(stdout=json.dumps([{"number": 42}]))

    assert existing_revert_pr_exists("abc123def456789", "owner/repo") is True

    # Search uses the 12-char short SHA + the title prefix.
    cmd = mock_run.call_args[0][0]
    search_idx = cmd.index("--search")
    search_value = cmd[search_idx + 1]
    assert "abc123def456" in search_value
    assert PR_TITLE_PREFIX in search_value


def test_existing_revert_pr_exists_false_when_gh_returns_empty(mocker):
    mock_run = mocker.patch("scripts.auto_revert.subprocess.run")
    mock_run.return_value = MagicMock(stdout="[]")

    assert existing_revert_pr_exists("abc", "owner/repo") is False


# === render_pr_body ===


def _deploy(sha: str = "abc123def4567890", status: str = "build_failed") -> FailedDeploy:
    return FailedDeploy(
        deploy_id="dep-1",
        commit_sha=sha,
        status=status,
        finished_at="2026-04-26T20:00:00Z",
        service_id="srv-test",
    )


def test_render_pr_body_includes_deploy_metadata():
    body = render_pr_body(_deploy(), migrations_touched=False)

    assert "dep-1" in body
    assert "srv-test" in body
    assert "build_failed" in body
    assert "abc123def456" in body  # short SHA
    assert "abc123def4567890" in body  # full SHA
    assert "2026-04-26T20:00:00Z" in body


def test_render_pr_body_omits_migration_warning_when_no_migrations():
    body = render_pr_body(_deploy(), migrations_touched=False)

    assert "Migration touched" not in body


def test_render_pr_body_includes_migration_warning_when_migrations_touched():
    body = render_pr_body(_deploy(), migrations_touched=True)

    assert "Migration touched" in body
    assert MIGRATIONS_PREFIX in body
    assert "rollback migration" in body


# === process_deploy ===


def test_process_deploy_skips_revert_of_revert(mocker):
    mocker.patch("scripts.auto_revert.is_revert_commit", return_value=True)
    open_pr = mocker.patch("scripts.auto_revert.open_revert_pr")

    result = process_deploy(_deploy(), repo="owner/repo", dry_run=False)

    assert result is None
    open_pr.assert_not_called()


def test_process_deploy_skips_when_revert_pr_already_exists(mocker):
    mocker.patch("scripts.auto_revert.is_revert_commit", return_value=False)
    mocker.patch("scripts.auto_revert.existing_revert_pr_exists", return_value=True)
    open_pr = mocker.patch("scripts.auto_revert.open_revert_pr")

    result = process_deploy(_deploy(), repo="owner/repo", dry_run=False)

    assert result is None
    open_pr.assert_not_called()


def test_process_deploy_dry_run_does_not_open_pr(mocker, capsys):
    mocker.patch("scripts.auto_revert.is_revert_commit", return_value=False)
    mocker.patch("scripts.auto_revert.existing_revert_pr_exists", return_value=False)
    mocker.patch("scripts.auto_revert.commit_touches_migrations", return_value=False)
    open_pr = mocker.patch("scripts.auto_revert.open_revert_pr")

    result = process_deploy(_deploy(), repo="owner/repo", dry_run=True)

    assert result is None
    open_pr.assert_not_called()
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "abc123def456" in out


def test_process_deploy_opens_normal_pr_when_no_migrations(mocker):
    mocker.patch("scripts.auto_revert.is_revert_commit", return_value=False)
    mocker.patch("scripts.auto_revert.existing_revert_pr_exists", return_value=False)
    mocker.patch("scripts.auto_revert.commit_touches_migrations", return_value=False)
    open_pr = mocker.patch("scripts.auto_revert.open_revert_pr", return_value="https://pr/123")

    result = process_deploy(_deploy(), repo="owner/repo", dry_run=False)

    assert result == "https://pr/123"
    open_pr.assert_called_once()
    assert open_pr.call_args.kwargs["draft"] is False


def test_process_deploy_opens_draft_pr_when_migrations_touched(mocker):
    mocker.patch("scripts.auto_revert.is_revert_commit", return_value=False)
    mocker.patch("scripts.auto_revert.existing_revert_pr_exists", return_value=False)
    mocker.patch("scripts.auto_revert.commit_touches_migrations", return_value=True)
    open_pr = mocker.patch("scripts.auto_revert.open_revert_pr", return_value="https://pr/124")

    result = process_deploy(_deploy(), repo="owner/repo", dry_run=False)

    assert result == "https://pr/124"
    assert open_pr.call_args.kwargs["draft"] is True


# === open_revert_pr ===


def test_open_revert_pr_passes_draft_flag_to_gh(mocker):
    """Sanity check the `--draft` flag is added when draft=True."""
    run = mocker.patch("scripts.auto_revert.subprocess.run")
    run.return_value = MagicMock(stdout="https://github.com/owner/repo/pull/9\n")

    from scripts.auto_revert import open_revert_pr

    open_revert_pr(_deploy(), draft=True, repo="owner/repo")

    # Find the gh pr create invocation.
    pr_create_calls = [
        c for c in run.call_args_list if c.args and c.args[0][:3] == ["gh", "pr", "create"]
    ]
    assert len(pr_create_calls) == 1
    assert "--draft" in pr_create_calls[0].args[0]


def test_open_revert_pr_omits_draft_flag_when_not_draft(mocker):
    run = mocker.patch("scripts.auto_revert.subprocess.run")
    run.return_value = MagicMock(stdout="https://github.com/owner/repo/pull/9\n")

    from scripts.auto_revert import open_revert_pr

    open_revert_pr(_deploy(), draft=False, repo="owner/repo")

    pr_create_calls = [
        c for c in run.call_args_list if c.args and c.args[0][:3] == ["gh", "pr", "create"]
    ]
    assert "--draft" not in pr_create_calls[0].args[0]


def test_open_revert_pr_labels_draft_with_needs_human_review(mocker):
    run = mocker.patch("scripts.auto_revert.subprocess.run")
    run.return_value = MagicMock(stdout="https://github.com/owner/repo/pull/9\n")

    from scripts.auto_revert import open_revert_pr

    open_revert_pr(_deploy(), draft=True, repo="owner/repo")

    label_calls = [
        c for c in run.call_args_list if c.args and c.args[0][:3] == ["gh", "pr", "edit"]
    ]
    assert len(label_calls) == 1
    assert NEEDS_HUMAN_LABEL in label_calls[0].args[0]


def test_open_revert_pr_does_not_label_when_not_draft(mocker):
    run = mocker.patch("scripts.auto_revert.subprocess.run")
    run.return_value = MagicMock(stdout="https://github.com/owner/repo/pull/9\n")

    from scripts.auto_revert import open_revert_pr

    open_revert_pr(_deploy(), draft=False, repo="owner/repo")

    label_calls = [
        c for c in run.call_args_list if c.args and c.args[0][:3] == ["gh", "pr", "edit"]
    ]
    assert label_calls == []


# === Sanity: subprocess.CalledProcessError survives the per-deploy try/except ===


def test_process_deploy_propagates_called_process_error(mocker):
    """process_deploy itself doesn't catch — main() does. Pin the boundary."""
    mocker.patch(
        "scripts.auto_revert.is_revert_commit",
        side_effect=subprocess.CalledProcessError(1, "git"),
    )

    with pytest.raises(subprocess.CalledProcessError):
        process_deploy(_deploy(), repo="owner/repo", dry_run=False)
