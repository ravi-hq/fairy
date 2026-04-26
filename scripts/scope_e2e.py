"""Compute which e2e tests to run for a PR based on changed source files.

Maps source paths to e2e test files. Outputs the test selection plus an
E2E_RUNTIMES value. Designed for CI: pair with an `AOD_API_TOKEN` check
so the actual pytest invocation is gated separately.

The mapping is intentionally coarse — when in doubt, fall through to
`ALL` rather than risk missing a regression. A few seconds of extra
runtime is cheaper than a missed bug.

Usage
-----
    uv run python -m scripts.scope_e2e
    uv run python -m scripts.scope_e2e --base-sha <SHA>
    uv run python -m scripts.scope_e2e --format=github  # writes $GITHUB_OUTPUT entries
    uv run python -m scripts.scope_e2e --format=shell   # eval-friendly RUNTIMES=... TESTS=...

Exit code is always 0; a no-op scope (nothing to run) emits empty TESTS.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
E2E_DIR = "tests/e2e"

# All e2e test files. Used when a rule's target is "ALL".
ALL_E2E: tuple[str, ...] = (
    "test_agents.py",
    "test_environments.py",
    "test_sessions.py",
    "test_skills.py",
    "test_mcp.py",
)

# Per-rule target shapes:
#   "ALL"          — every file in ALL_E2E
#   "PASS_THROUGH" — for changed files under tests/e2e/, run that exact file
#   tuple[str]     — explicit list of test filenames (relative to tests/e2e/)
#
# Order is significant for readability only; the algorithm unions matches.
# Prefix-match: a rule matches if a changed file starts with the rule's
# path prefix. Trailing "/" makes a directory rule.
RULES: tuple[tuple[str, object], ...] = (
    # Cross-cutting concerns: any change here can break every endpoint.
    ("src/agent_on_demand/auth.py", "ALL"),
    ("src/agent_on_demand/crypto.py", "ALL"),
    ("src/config/", "ALL"),
    ("src/agent_on_demand/runtimes/__init__.py", "ALL"),
    ("src/agent_on_demand/runtimes/base.py", "ALL"),
    ("src/agent_on_demand/observability.py", "ALL"),
    ("src/agent_on_demand/signals.py", "ALL"),
    ("src/agent_on_demand/tasks.py", "ALL"),
    ("src/agent_on_demand/urls.py", "ALL"),
    # Versioning + state-machine helpers — scoped to where they're used.
    (
        "src/agent_on_demand/versioning.py",
        ("test_agents.py", "test_environments.py"),
    ),
    ("src/agent_on_demand/session_state.py", ("test_sessions.py",)),
    # Resource-specific code paths.
    ("src/agent_on_demand/views/agents.py", ("test_agents.py",)),
    ("src/agent_on_demand/models/agents.py", ("test_agents.py",)),
    ("src/agent_on_demand/views/environments.py", ("test_environments.py",)),
    ("src/agent_on_demand/models/environments.py", ("test_environments.py",)),
    ("src/agent_on_demand/views/sessions.py", ("test_sessions.py",)),
    ("src/agent_on_demand/models/sessions.py", ("test_sessions.py",)),
    ("src/agent_on_demand/stream.py", ("test_sessions.py",)),
    # Session orchestration touches every test that creates a real session.
    (
        "src/agent_on_demand/session_service/",
        ("test_sessions.py", "test_skills.py", "test_mcp.py"),
    ),
    # Runtime-specific files: a change to e.g. codex.py needs the session-
    # spawning tests run with E2E_RUNTIMES=codex (handled separately below).
    (
        "src/agent_on_demand/runtimes/claude.py",
        ("test_sessions.py", "test_skills.py", "test_mcp.py"),
    ),
    (
        "src/agent_on_demand/runtimes/codex.py",
        ("test_sessions.py", "test_skills.py", "test_mcp.py"),
    ),
    (
        "src/agent_on_demand/runtimes/gemini.py",
        ("test_sessions.py", "test_skills.py", "test_mcp.py"),
    ),
    (
        "src/agent_on_demand/runtimes/opencode.py",
        ("test_sessions.py", "test_skills.py", "test_mcp.py"),
    ),
    # Pydantic models common to multiple resources.
    ("src/agent_on_demand/models_catalog.py", "ALL"),
    ("src/agent_on_demand/models/auth.py", "ALL"),
    # E2E tests themselves: run only the changed file.
    (E2E_DIR + "/", "PASS_THROUGH"),
)

# Source path → runtime name. Touching one of these scopes E2E_RUNTIMES
# to that runtime; touching base/__init__ scopes to ALL.
RUNTIME_PATHS: dict[str, str] = {
    "claude": "src/agent_on_demand/runtimes/claude.py",
    "codex": "src/agent_on_demand/runtimes/codex.py",
    "gemini": "src/agent_on_demand/runtimes/gemini.py",
    "opencode": "src/agent_on_demand/runtimes/opencode.py",
}

# When no runtime-specific file changed, default to claude (cheapest).
DEFAULT_RUNTIME = "claude"


def compute_scope(changed_files: Iterable[str]) -> dict:
    """Pure mapping function: changed files → {tests, runtimes}.

    `tests` is a sorted list of paths relative to the repo root.
    `runtimes` is a sorted list of runtime names; empty means "no e2e
    coverage relevant to this change."
    """
    tests: set[str] = set()
    runtimes: set[str] = set()
    runtime_path_changed = False

    files = sorted(set(changed_files))

    for path in files:
        # Runtime-specific selection.
        for runtime, runtime_path in RUNTIME_PATHS.items():
            if path == runtime_path:
                runtimes.add(runtime)
                runtime_path_changed = True
                break

        # Test selection.
        for prefix, target in RULES:
            if not path.startswith(prefix):
                continue
            if target == "ALL":
                tests.update(f"{E2E_DIR}/{f}" for f in ALL_E2E)
            elif target == "PASS_THROUGH":
                # Only direct children of tests/e2e/ that look like e2e tests.
                if path.startswith(E2E_DIR + "/test_") and path.endswith(".py"):
                    tests.add(path)
            else:
                tests.update(f"{E2E_DIR}/{f}" for f in target)
            # Continue checking other rules — multiple may apply (e.g. a
            # PR touching auth.py and views/agents.py picks up both).

    # If a runtime file changed, scope to that runtime; otherwise default.
    # If no test scope at all matched, runtimes don't matter — leave empty
    # so callers can short-circuit.
    if not tests:
        return {"tests": [], "runtimes": []}

    if not runtime_path_changed:
        runtimes.add(DEFAULT_RUNTIME)

    return {"tests": sorted(tests), "runtimes": sorted(runtimes)}


def _git_changed_files(base_sha: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base_sha}...HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def _resolve_base_sha(arg: str | None) -> str:
    if arg:
        return arg
    out = subprocess.run(
        ["git", "merge-base", "origin/main", "HEAD"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        # Fallback to main if origin/main isn't fetched.
        out = subprocess.run(
            ["git", "merge-base", "main", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    return out.stdout.strip()


def _format_output(scope: dict, fmt: str) -> str:
    tests = " ".join(scope["tests"])
    runtimes = ",".join(scope["runtimes"])
    if fmt == "shell":
        return f"TESTS={tests!r}\nRUNTIMES={runtimes!r}\n"
    if fmt == "github":
        return f"tests={tests}\nruntimes={runtimes}\n"
    if fmt == "json":
        import json

        return json.dumps(scope, indent=2) + "\n"
    # human
    if not tests:
        return "No e2e tests in scope for this change.\n"
    lines = [
        f"E2E tests in scope ({len(scope['tests'])}):",
        *[f"  - {t}" for t in scope["tests"]],
        f"Runtimes: {runtimes or '(none)'}",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-sha",
        default=None,
        help="Compute diff against this SHA. Defaults to `git merge-base origin/main HEAD`.",
    )
    parser.add_argument(
        "--format",
        default="human",
        choices=["human", "shell", "github", "json"],
        help="Output format. `github` writes `tests=...\\nruntimes=...` for $GITHUB_OUTPUT.",
    )
    args = parser.parse_args(argv)

    base = _resolve_base_sha(args.base_sha)
    changed = _git_changed_files(base)
    scope = compute_scope(changed)

    output = _format_output(scope, args.format)
    if args.format == "github":
        # Write to $GITHUB_OUTPUT if available, otherwise stdout.
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a") as f:
                f.write(output)
            return 0
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
