"""Smoke test: every API route under /agents, /environments, /sessions
demands authentication. Public routes (/, /ui/*, /health) are explicitly
allowed.

Catches a regression like "developer forgets `@require_api_key` on a new
endpoint" — without this test, the endpoint would respond 200 to
unauthenticated calls, exposing whatever it returns.
"""

from __future__ import annotations

import uuid

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver


# Path prefixes that are intentionally public OR that authenticate via a
# different mechanism than the API-key check this test focuses on.
PUBLIC_PREFIXES = (
    "/",  # landing page
    "/ui/",  # UI authenticates via Django sessions, not API key
    "/admin/",  # Django admin authenticates via Django sessions
    "/health",  # Render auto-rollback probe
)


def _flatten_patterns(resolver, prefix: str = "") -> list[str]:
    """Walk URLResolver/URLPattern tree, returning each leaf URL pattern as
    a string with placeholders for converters (e.g. `<uuid:agent_id>`)."""
    out: list[str] = []
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            sub_prefix = prefix + str(entry.pattern)
            out.extend(_flatten_patterns(entry, sub_prefix))
        elif isinstance(entry, URLPattern):
            out.append(prefix + str(entry.pattern))
    return out


def _is_public(path: str) -> bool:
    if path == "/":
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES if p not in ("/",))


def _materialize_path(pattern: str) -> str:
    """Replace `<uuid:foo>` etc. converters with concrete values so the
    Django test client's URL resolver matches. We're not asserting on the
    response body — only on the status code — so any well-formed UUID
    works."""
    out = pattern
    while "<" in out:
        start = out.index("<")
        end = out.index(">", start)
        token = out[start + 1 : end]
        # token is "converter:name" (e.g. "uuid:agent_id") or just "name".
        if token.startswith("uuid:"):
            replacement = str(uuid.uuid4())
        elif token.startswith("int:"):
            replacement = "0"
        elif token.startswith("str:"):
            replacement = "x"
        elif token.startswith("path:"):
            replacement = "x"
        elif token.startswith("slug:"):
            replacement = "x"
        else:
            # Default Django converter (no prefix) is `str`.
            replacement = "x"
        out = out[:start] + replacement + out[end + 1 :]
    if not out.startswith("/"):
        out = "/" + out
    return out


# We want a path that matches each api pattern; trying every HTTP method
# is too noisy. The contract here is: an unauthenticated request to any
# api route should NOT return 200/2xx/3xx-with-data. 401 is ideal; 404
# (route doesn't accept GET) and 405 (wrong method) are also acceptable
# refusals because they don't expose data.
ACCEPTABLE_REFUSAL_STATUSES = {401, 404, 405}


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
def test_every_api_route_refuses_unauthenticated_requests(method: str):
    """For every URL pattern in the resolver tree (excluding intentionally
    public ones), an unauthenticated request must NOT return a successful
    status. The status can be 401 (preferred) or 404/405 (depending on
    the method) — what matters is that no data leaks."""
    client = Client()
    resolver = get_resolver()
    patterns = _flatten_patterns(resolver)

    failures: list[str] = []
    for pat in patterns:
        path = _materialize_path(pat)
        if _is_public(path):
            continue
        # Skip the SSE stream endpoint — Django's sync test client doesn't
        # play well with StreamingHttpResponse and stream auth is covered
        # in test_stream.py.
        if path.endswith("/stream"):
            continue
        resp = getattr(client, method)(path)
        if resp.status_code not in ACCEPTABLE_REFUSAL_STATUSES:
            failures.append(f"{method.upper()} {path} → {resp.status_code}")

    assert not failures, (
        "Unauthenticated requests to api routes must return 401/404/405. "
        f"Got non-refusal responses for: {failures}"
    )


def test_public_landing_route_responds_unauthenticated(client: Client):
    """Sanity check: at least one public route must respond 200 unauth so
    the test above isn't trivially passing because everything is locked."""
    resp = client.get("/")
    assert resp.status_code == 200


def test_health_responds_unauthenticated(client: Client, db):
    """Render's auto-rollback probe must work without auth. Pinning here
    so the loop above doesn't accidentally start enforcing auth on /health."""
    resp = client.get("/health")
    assert resp.status_code == 200
