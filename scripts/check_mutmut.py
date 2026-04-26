"""Run mutmut and assert the result matches our quality bar.

Fails if:
  - any new mutant survives that's not in the documented equivalent set,
  - any mutant times out (indicates async/test runner trouble), or
  - any mutant is marked suspicious.

The KNOWN_EQUIVALENT mutants below are semantically identical to the
original code under any input — see the explanations alongside each entry.
They cannot be killed by tests; the only way to remove them from this list
is to change the production code.

Run: `uv run python -m scripts.check_mutmut`
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATS_PATH = REPO_ROOT / "mutants" / "mutmut-cicd-stats.json"

KNOWN_EQUIVALENT: set[str] = {
    # Header default "" -> "XXXX". Both fail startswith("Bearer "), so the
    # missing-header path returns the same 401 response either way. There
    # is no way to distinguish these without rewriting the check (e.g. into
    # a try/except KeyError), which is worse code for no real benefit.
    "agent_on_demand.auth.x__check_api_key_sync__mutmut_8",
    "agent_on_demand.auth.x__check_api_key_async__mutmut_8",
    # `env.packages.get(manager, [])` -> `.get(manager, None)` and
    # `.get(manager, )` (no default). The very next line is
    # `if not pkgs: continue`, which treats `[]`, `None`, and any other
    # falsy value identically — so the missing-key path always continues
    # in both original and mutant. The only way to distinguish would be to
    # remove the `if not pkgs` guard, which would make `package_commands`
    # raise on a missing manager key. Keeping the guard.
    "agent_on_demand.session_service.provision_script.x_build_provision_script__mutmut_27",
    "agent_on_demand.session_service.provision_script.x_build_provision_script__mutmut_29",
}


def _run_mutmut() -> None:
    print("Running mutmut...", flush=True)
    subprocess.run(["uv", "run", "mutmut", "run"], cwd=REPO_ROOT, check=False)
    subprocess.run(
        ["uv", "run", "mutmut", "export-cicd-stats"],
        cwd=REPO_ROOT,
        check=True,
    )


def _surviving_mutants() -> set[str]:
    out = subprocess.run(
        ["uv", "run", "mutmut", "results"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    survived: set[str] = set()
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.endswith(": survived"):
            survived.add(line.removesuffix(": survived").strip())
    return survived


def main() -> int:
    _run_mutmut()
    stats = json.loads(STATS_PATH.read_text())
    survived = _surviving_mutants()
    new = survived - KNOWN_EQUIVALENT
    stale = KNOWN_EQUIVALENT - survived

    print()
    print(
        f"Mutants: {stats['killed']}/{stats['total']} killed, "
        f"{stats['survived']} survived, "
        f"{stats['timeout']} timeout, "
        f"{stats['suspicious']} suspicious"
    )

    failures: list[str] = []
    if stats["timeout"]:
        failures.append(f"{stats['timeout']} mutants timed out (async test runner issue?)")
    if stats["suspicious"]:
        failures.append(f"{stats['suspicious']} mutants marked suspicious")
    if new:
        failures.append("New surviving mutants (add tests to kill them):")
        for name in sorted(new):
            failures.append(f"  - {name}")
            failures.append(f"    inspect: uv run mutmut show {name}")

    if failures:
        print()
        for line in failures:
            print(line)
        return 1

    if stale:
        print()
        print(
            "Note: these documented-equivalent mutants are no longer present "
            "(production code may have changed):"
        )
        for name in sorted(stale):
            print(f"  - {name}")
        print("Update KNOWN_EQUIVALENT in scripts/check_mutmut.py to remove them.")

    print()
    print("OK — mutation testing passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
