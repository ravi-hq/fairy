"""Smoke test: every file CLAUDE.md calls out as a "danger zone" actually
exists, and every danger-zone file is mutation-tested where the doc says
it should be.

CLAUDE.md asserts:
  - src/agent_on_demand/auth.py — bearer-token check, mutation-tested
  - src/agent_on_demand/crypto.py — encryption wrapper, mutation-tested
  - src/agent_on_demand/versioning.py — optimistic concurrency, mutation-tested
  - src/agent_on_demand/session_state.py — state machine, mutation-tested

If any of these files is deleted/renamed without updating CLAUDE.md, this
test fails with a clear pointer. The mutation-test config in pyproject.toml
is also asserted to reference each one.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

DANGER_ZONE_FILES = [
    "src/agent_on_demand/auth.py",
    "src/agent_on_demand/crypto.py",
    "src/agent_on_demand/versioning.py",
    "src/agent_on_demand/session_state.py",
]


def test_every_danger_zone_file_exists():
    """Each file CLAUDE.md calls out as a danger zone must exist on disk.
    A rename or accidental delete fails this test before CI runs the
    actual gates against the file."""
    for relpath in DANGER_ZONE_FILES:
        path = REPO_ROOT / relpath
        assert path.exists(), (
            f"Danger-zone file {relpath} does not exist. If you renamed it, "
            f"update both CLAUDE.md and `[tool.mutmut].paths_to_mutate` in "
            f"pyproject.toml."
        )


def test_every_danger_zone_file_is_mutation_tested():
    """Each danger-zone file must be in the mutmut paths_to_mutate config.
    CLAUDE.md asserts they are; this pins the assertion mechanically.

    A file that's named in CLAUDE.md but missing from mutmut config gets
    the danger-zone *prose* coverage but not the actual mutation-test gate
    — silently weakening protection."""
    pyproject = REPO_ROOT / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text())
    mutated = set(config["tool"]["mutmut"]["paths_to_mutate"])
    for relpath in DANGER_ZONE_FILES:
        assert relpath in mutated, (
            f"{relpath} is documented as a danger-zone in CLAUDE.md but is "
            f"not in `[tool.mutmut].paths_to_mutate` — mutation testing "
            f"isn't actually running against it."
        )


def test_claude_md_danger_zones_section_references_each_file():
    """The danger-zones section text in CLAUDE.md must reference each file.
    Catches the case where a developer adds a file to mutmut config but
    forgets to document it (or vice versa)."""
    claude_md = (REPO_ROOT / "CLAUDE.md").read_text()
    for relpath in DANGER_ZONE_FILES:
        assert relpath in claude_md, (
            f"{relpath} is in mutmut config but not mentioned in CLAUDE.md. "
            f"Add it to the 'Files: any change here requires explicit human "
            f"review' list under '## Danger zones'."
        )
