"""Discovery test: every pydantic *Request model in views/ must be tracked
in scripts/check_request_schemas.py's MODELS list.

The check-schemas CI gate snapshots a fixed list of models. If a developer
adds a new `XRequest(BaseModel)` to a view but forgets to add it to that
list, the gate silently skips snapshotting it — meaning future drift to
that new model goes undetected.

This test reflects the truth in the other direction: for every Request
model currently in views/, assert it's tracked. Catches the
forgot-to-add case at PR time.
"""

from __future__ import annotations

import importlib
import inspect

import pytest
from pydantic import BaseModel


# Modules to scan for Request models.
VIEW_MODULES = [
    "agent_on_demand.views.agents",
    "agent_on_demand.views.environments",
    "agent_on_demand.views.sessions",
]


def _discover_request_models() -> set[tuple[str, str]]:
    """Walk every view module and return (module_path, class_name) tuples for
    every BaseModel subclass whose name ends with `Request`.

    `Request` is the convention these models follow; widening this would
    catch helper models the snapshot intentionally excludes (e.g.
    GitHubRepoResource is in MODELS but doesn't end in Request — the
    discovery still asserts on all *Request models, ignoring helpers)."""
    found: set[tuple[str, str]] = set()
    for module_path in VIEW_MODULES:
        module = importlib.import_module(module_path)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__ == module_path
                and name.endswith("Request")
            ):
                found.add((module_path, name))
    return found


def test_every_request_model_in_views_is_tracked_by_schema_check():
    from scripts.check_request_schemas import MODELS

    tracked = set(MODELS)
    discovered = _discover_request_models()

    missing = discovered - tracked
    assert not missing, (
        f"Found Request models in views/ that aren't in scripts/check_request_schemas.py "
        f"MODELS: {sorted(missing)}. Add them to MODELS so the check-schemas gate "
        f"snapshots their JSON schema."
    )


def test_every_tracked_model_still_exists():
    """The reverse direction: anything in MODELS must still be importable.
    A model rename without updating MODELS would land silently — until
    `make check-schemas` ImportErrors at CI runtime. Catch it here."""
    from scripts.check_request_schemas import MODELS

    for module_path, attr in MODELS:
        module = importlib.import_module(module_path)
        cls = getattr(module, attr, None)
        assert cls is not None, f"{module_path}.{attr} is in MODELS but not in the module"
        assert issubclass(cls, BaseModel), (
            f"{module_path}.{attr} is in MODELS but is not a pydantic BaseModel"
        )


def test_check_request_schemas_snapshot_is_clean_against_current_models():
    """Smoke test the script's main contract: running --write would produce
    output identical to the committed snapshot. (Equivalent to running
    `make check-schemas` but invoked in-process so a CI miss is caught at
    unit-test time.)"""
    import json
    from pathlib import Path

    from scripts.check_request_schemas import REPO_ROOT, SNAPSHOT_PATH, _current_schemas

    # _current_schemas reads from MODELS and dumps the live schema for each.
    current = _current_schemas()
    snapshot_path = Path(SNAPSHOT_PATH)
    if not snapshot_path.exists():
        pytest.skip(
            f"Snapshot not present at {snapshot_path}; nothing to compare against. "
            f"Run `make snapshot-schemas` to create it."
        )
    snapshot = json.loads(snapshot_path.read_text())
    assert current == snapshot, (
        "Live request models drift from docs/request_schemas.json. "
        "Run `make snapshot-schemas` if the change is intentional."
    )
    assert REPO_ROOT.exists(), "REPO_ROOT path resolved by the script must exist"
