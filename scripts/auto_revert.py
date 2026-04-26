"""Auto-open revert PRs for failed Render deploys.

Polls Render's deploys API for the configured service, finds deploys that
finished as failed, and opens a `git revert` PR for each unique failing
commit. Idempotent: skips a commit if a revert PR already exists for it,
and skips reverts of reverts so a botched revert can't loop on itself.

If the failing commit modified files under `src/agent_on_demand/migrations/`,
the PR is opened as a **draft** with the `needs-human-review` label —
reverting code alone does not undo a migration, so a human has to decide
whether to add a follow-up rollback migration or merge as-is.

Designed to run from a GitHub Actions cron. Render API key + GitHub token
come from env vars (`RENDER_API_KEY`, `GH_TOKEN`); no cursor file is needed
because dedup is driven by the existing-PR check.

Usage
-----
    uv run python -m scripts.auto_revert \
        --service-id srv-XXXXXXXXXXXX \
        --repo ravi-hq/fairy

    # Dry run (no PRs opened, no branches pushed):
    uv run python -m scripts.auto_revert \
        --service-id srv-XXXXXXXXXXXX \
        --repo ravi-hq/fairy --dry-run

Required env: RENDER_API_KEY. GH_TOKEN required when not --dry-run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

RENDER_API = "https://api.render.com/v1"

# Render deploy lifecycle terminal states we treat as "this commit is bad."
# Render auto-rolls-forward to the previous live commit on failure, but
# `main` still has the offending SHA — that's what we're here to fix.
#
# `canceled` is intentionally excluded: Render marks a deploy `canceled`
# when a newer deploy supersedes it — the normal result of pushing two
# commits in quick succession — and the earlier commit isn't actually bad.
FAILED_STATUSES = frozenset({"build_failed", "update_failed"})

# Path prefix that identifies a migration file. Matches the layout pinned
# in CLAUDE.md "Danger zones": migrations are forward-only in prod.
MIGRATIONS_PREFIX = "src/agent_on_demand/migrations/"

PR_TITLE_PREFIX = "Auto-revert:"
NEEDS_HUMAN_LABEL = "needs-human-review"


@dataclass(frozen=True)
class FailedDeploy:
    deploy_id: str
    commit_sha: str
    status: str
    finished_at: str
    service_id: str


def fetch_failed_deploys(service_id: str, api_key: str, limit: int = 20) -> list[FailedDeploy]:
    """Hit Render's deploys endpoint and return the recent failed ones.

    Render returns deploys newest-first; we filter to terminal-failure
    states and drop anything without a commit SHA (e.g. manual
    redeploys triggered before a commit existed).
    """
    resp = requests.get(
        f"{RENDER_API}/services/{service_id}/deploys",
        params={"limit": limit},
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    out: list[FailedDeploy] = []
    for entry in resp.json():
        # Render wraps each item as {"deploy": {...}, "cursor": "..."}.
        deploy = entry["deploy"] if "deploy" in entry else entry
        if deploy.get("status") not in FAILED_STATUSES:
            continue
        commit = deploy.get("commit") or {}
        sha = commit.get("id")
        if not sha:
            continue
        out.append(
            FailedDeploy(
                deploy_id=deploy["id"],
                commit_sha=sha,
                status=deploy["status"],
                finished_at=deploy.get("finishedAt") or deploy.get("createdAt", ""),
                service_id=service_id,
            )
        )
    return out


def is_revert_commit(sha: str) -> bool:
    """A commit whose subject starts with 'Revert ' shouldn't be reverted again."""
    msg = subprocess.run(
        ["git", "log", "-1", "--pretty=%s", sha],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return msg.startswith("Revert ")


def commit_touches_migrations(sha: str) -> bool:
    """True if the commit changed any file under MIGRATIONS_PREFIX."""
    result = subprocess.run(
        ["git", "show", "--name-only", "--pretty=", sha],
        check=True,
        capture_output=True,
        text=True,
    )
    return any(line.startswith(MIGRATIONS_PREFIX) for line in result.stdout.splitlines())


def existing_revert_pr_exists(sha: str, repo: str) -> bool:
    """True if any open or merged PR has our auto-revert title for this SHA.

    The 12-char short SHA in the title is the dedup key; the existence
    check covers both still-open PRs and already-landed reverts.
    """
    short = sha[:12]
    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--search",
            f"in:title {PR_TITLE_PREFIX} {short}",
            "--json",
            "number",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(json.loads(result.stdout))


def render_pr_body(deploy: FailedDeploy, *, migrations_touched: bool) -> str:
    """Format the PR body. Pure function — easy to pin in tests."""
    short = deploy.commit_sha[:12]
    lines = [
        f"Auto-opened by `scripts/auto_revert.py` after Render deploy "
        f"`{deploy.deploy_id}` finished as `{deploy.status}`.",
        "",
        f"- **Service**: `{deploy.service_id}`",
        f"- **Failed commit**: `{short}` (full: `{deploy.commit_sha}`)",
        f"- **Finished at**: {deploy.finished_at}",
        "",
    ]
    if migrations_touched:
        lines += [
            "## ⚠️ Migration touched",
            "",
            "This commit modified files under "
            f"`{MIGRATIONS_PREFIX}`. Reverting the code alone does **not** "
            "undo the migration. Decide on a follow-up:",
            "",
            "- If the migration was additive and harmless, merge this PR as-is.",
            "- If the migration must be reversed, add a new rollback migration "
            "to this PR before merging.",
            "- If the deploy is acceptable and the failure was transient, close this PR.",
            "",
        ]
    lines += [
        "## What to do",
        "",
        "1. Verify the failed deploy in the Render dashboard.",
        "2. If the revert is correct, **merge** this PR.",
        "3. If the failure was a flake or you've already hotfixed forward, **close** this PR.",
        "",
        "Generated by `.github/workflows/auto-revert.yml`.",
    ]
    return "\n".join(lines)


def open_revert_pr(
    deploy: FailedDeploy, *, draft: bool, migrations_touched: bool, repo: str
) -> str:
    """Create a branch, run git revert, push, open PR. Returns PR URL.

    `draft` and `migrations_touched` happen to coincide today (a touched
    migration is the only reason we draft), but they're passed
    independently so the PR-body warning stays tied to its actual cause
    if a future change adds another reason to draft.
    """
    short = deploy.commit_sha[:12]
    branch = f"auto-revert/{short}"

    subprocess.run(["git", "fetch", "origin", "main"], check=True)
    subprocess.run(["git", "checkout", "-B", branch, "origin/main"], check=True)
    subprocess.run(
        ["git", "revert", "--no-edit", deploy.commit_sha],
        check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", branch, "--force-with-lease"],
        check=True,
    )

    title = f"{PR_TITLE_PREFIX} {short} ({deploy.status})"
    body = render_pr_body(deploy, migrations_touched=migrations_touched)

    cmd = [
        "gh",
        "pr",
        "create",
        "--repo",
        repo,
        "--base",
        "main",
        "--head",
        branch,
        "--title",
        title,
        "--body",
        body,
    ]
    if draft:
        cmd.append("--draft")
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    pr_url = result.stdout.strip()

    if draft:
        # Label may not exist in the repo yet; don't crash if so.
        subprocess.run(
            ["gh", "pr", "edit", pr_url, "--repo", repo, "--add-label", NEEDS_HUMAN_LABEL],
            check=False,
        )
    return pr_url


def process_deploy(deploy: FailedDeploy, *, repo: str, dry_run: bool) -> str | None:
    """Decide and act on a single deploy. Returns PR URL if one was opened."""
    if is_revert_commit(deploy.commit_sha):
        return None
    if existing_revert_pr_exists(deploy.commit_sha, repo):
        return None
    migrations_touched = commit_touches_migrations(deploy.commit_sha)
    if dry_run:
        marker = "draft " if migrations_touched else ""
        print(
            f"[dry-run] would open {marker}revert PR for "
            f"{deploy.commit_sha[:12]} (deploy {deploy.deploy_id})"
        )
        return None
    return open_revert_pr(
        deploy, draft=migrations_touched, migrations_touched=migrations_touched, repo=repo
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Open revert PRs for failed Render deploys.")
    parser.add_argument("--service-id", required=True, help="Render service ID, e.g. srv-XXXX")
    parser.add_argument("--repo", required=True, help="GitHub repo, e.g. ravi-hq/fairy")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many recent deploys to inspect (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended actions without opening PRs",
    )
    args = parser.parse_args()

    api_key = os.environ.get("RENDER_API_KEY")
    if not api_key:
        print("error: RENDER_API_KEY env var not set", file=sys.stderr)
        return 1
    if not args.dry_run and not os.environ.get("GH_TOKEN"):
        print(
            "error: GH_TOKEN env var not set (required for git push and gh pr create)",
            file=sys.stderr,
        )
        return 1

    deploys = fetch_failed_deploys(args.service_id, api_key, limit=args.limit)
    if not deploys:
        print("no failed deploys in the recent window")
        return 0

    # Dedup by commit SHA — Render often retries the same commit, and we
    # want one PR per bad commit, not one per deploy attempt.
    seen: set[str] = set()
    opened: list[str] = []
    for deploy in deploys:
        if deploy.commit_sha in seen:
            continue
        seen.add(deploy.commit_sha)
        try:
            url = process_deploy(deploy, repo=args.repo, dry_run=args.dry_run)
        except subprocess.CalledProcessError as exc:
            # One bad deploy shouldn't block the others. Surface the
            # failure in the action log; cron will retry next interval.
            print(
                f"error processing deploy {deploy.deploy_id} ({deploy.commit_sha[:12]}): {exc}",
                file=sys.stderr,
            )
            continue
        if url:
            opened.append(url)

    if opened:
        print(f"opened {len(opened)} revert PR(s):")
        for url in opened:
            print(f"  {url}")
    else:
        print(f"checked {len(seen)} unique failed commit(s); no new reverts to open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
