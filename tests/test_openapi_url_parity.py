"""Smoke test: docs/openapi.yaml documents exactly the URL patterns
declared in src/agent_on_demand/urls.py.

`scripts/validate_openapi.py` validates that the OpenAPI document is
*structurally* correct. `scripts/check_sdk_parity.py` checks the
Python SDK matches OpenAPI. Neither catches the case where the OpenAPI
document is internally consistent but DRIFTED from the actual code.

This test is the missing third leg: walk the URL resolver, normalize
each api path, and assert it appears in openapi.yaml's `paths`. The
reverse direction also runs — every documented path must resolve to a
real URL pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from django.urls import URLPattern, URLResolver, get_resolver


REPO_ROOT = Path(__file__).resolve().parent.parent
OPENAPI_PATH = REPO_ROOT / "docs" / "openapi.yaml"


# Excluded from the comparison: paths that aren't part of the public API
# surface and therefore intentionally aren't in OpenAPI.
NON_API_PREFIXES = (
    "/ui/",  # browser-only UI; documented in user-facing docs
    "/admin/",  # Django admin
    "/static/",  # static assets
)


def _flatten(resolver, prefix: str = "") -> list[str]:
    out: list[str] = []
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            out.extend(_flatten(entry, prefix + str(entry.pattern)))
        elif isinstance(entry, URLPattern):
            out.append(prefix + str(entry.pattern))
    return out


_CONVERTER_RE = re.compile(r"<(?:[^:>]+:)?([^>]+)>")


def _normalize_django_pattern(pat: str) -> str:
    """Convert Django URL pattern to OpenAPI path syntax:
    `agents/<uuid:agent_id>` → `/agents/{agent_id}`."""
    p = _CONVERTER_RE.sub(lambda m: "{" + m.group(1) + "}", pat)
    if not p.startswith("/"):
        p = "/" + p
    return p


def _api_paths_from_django() -> set[str]:
    resolver = get_resolver()
    paths: set[str] = set()
    for raw in _flatten(resolver):
        norm = _normalize_django_pattern(raw)
        if norm == "/":
            continue  # landing page; not part of the API
        if any(norm.startswith(prefix) for prefix in NON_API_PREFIXES):
            continue
        # Drop trailing-empty-segment artifacts from include() prefixes.
        if norm.endswith("/") and len(norm) > 1:
            norm = norm.rstrip("/")
        paths.add(norm)
    return paths


def _api_paths_from_openapi() -> set[str]:
    if not OPENAPI_PATH.exists():
        pytest.skip(f"OpenAPI not present at {OPENAPI_PATH}")
    spec = yaml.safe_load(OPENAPI_PATH.read_text())
    return set(spec.get("paths", {}))


def test_every_url_pattern_is_documented_in_openapi():
    django_paths = _api_paths_from_django()
    openapi_paths = _api_paths_from_openapi()
    missing = django_paths - openapi_paths
    assert not missing, (
        f"URL patterns missing from docs/openapi.yaml: {sorted(missing)}. "
        f"Add the path under `paths:` in the spec."
    )


def test_every_openapi_path_resolves_to_a_url_pattern():
    django_paths = _api_paths_from_django()
    openapi_paths = _api_paths_from_openapi()
    extra = openapi_paths - django_paths
    assert not extra, (
        f"docs/openapi.yaml documents paths that don't resolve to a Django URL: "
        f"{sorted(extra)}. Either remove the spec entry or add the route to urls.py."
    )
