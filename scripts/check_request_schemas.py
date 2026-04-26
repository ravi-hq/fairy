"""Snapshot the JSON schemas of all request-body pydantic models in views/.

Catches accidental drift in the public request contract — added/removed
fields, renamed fields, type changes, required vs. optional flips. Pairs
with `validate_openapi.py` (which checks routes/methods) and the
`docs/openapi.yaml` (which documents the contract for SDK consumers).

Usage
-----
Default (CI): compare current schemas against the committed snapshot.
    uv run python -m scripts.check_request_schemas

Regenerate the snapshot after an intentional contract change:
    uv run python -m scripts.check_request_schemas --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import django

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO_ROOT / "docs" / "request_schemas.json"


def _setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(REPO_ROOT / "src"))
    django.setup()


# Each entry: (module path, attribute name). The output dict is keyed
# "module.ClassName" so a renamed model surfaces as a clear diff.
MODELS = [
    ("agent_on_demand.views.agents", "CreateAgentRequest"),
    ("agent_on_demand.views.agents", "UpdateAgentRequest"),
    ("agent_on_demand.views.environments", "CreateEnvironmentRequest"),
    ("agent_on_demand.views.environments", "UpdateEnvironmentRequest"),
    ("agent_on_demand.views.sessions", "GitHubRepoResource"),
    ("agent_on_demand.views.sessions", "RunRequest"),
    ("agent_on_demand.views.sessions", "PromptRequest"),
]


def _current_schemas() -> dict:
    import importlib

    out: dict[str, dict] = {}
    for module_path, attr in MODELS:
        module = importlib.import_module(module_path)
        cls = getattr(module, attr)
        out[f"{module_path}.{attr}"] = cls.model_json_schema()
    return out


def _write_snapshot(schemas: dict) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w") as f:
        json.dump(schemas, f, indent=2, sort_keys=True)
        f.write("\n")


def _read_snapshot() -> dict | None:
    if not SNAPSHOT_PATH.exists():
        return None
    with open(SNAPSHOT_PATH) as f:
        return json.load(f)


def _diff_summary(current: dict, snapshot: dict) -> list[str]:
    """Human-readable summary of which models drifted, by name."""
    errors: list[str] = []
    cur_keys = set(current)
    snap_keys = set(snapshot)
    for added in sorted(cur_keys - snap_keys):
        errors.append(f"new model not in snapshot: {added}")
    for removed in sorted(snap_keys - cur_keys):
        errors.append(f"model removed (or renamed) from snapshot: {removed}")
    for name in sorted(cur_keys & snap_keys):
        if current[name] != snapshot[name]:
            errors.append(f"schema drift in {name}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate the snapshot file from current models. "
        "Use after an intentional contract change.",
    )
    args = parser.parse_args()

    _setup_django()
    current = _current_schemas()

    if args.write:
        _write_snapshot(current)
        print(f"Wrote {len(current)} request schemas to {SNAPSHOT_PATH.relative_to(REPO_ROOT)}")
        return 0

    snapshot = _read_snapshot()
    if snapshot is None:
        print(
            f"No snapshot at {SNAPSHOT_PATH.relative_to(REPO_ROOT)}. "
            "Run with --write to create one.",
            file=sys.stderr,
        )
        return 1

    errors = _diff_summary(current, snapshot)
    if errors:
        print("Request-schema drift detected:\n", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "\nIf this is an intentional contract change, regenerate the snapshot:\n"
            "    uv run python -m scripts.check_request_schemas --write\n"
            "and update docs/openapi.yaml in the same PR.",
            file=sys.stderr,
        )
        return 1

    print(f"Request schemas OK ({len(current)} models verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
