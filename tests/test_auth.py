"""Direct unit tests for the require_api_key decorator and its sync/async helpers.

These tests intentionally exercise auth without going through any specific view's
URL route, so they pin down the auth contract independently of session/agent
endpoints.
"""

import json
from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db import connection
from django.http import JsonResponse
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone as tz

from agent_on_demand.auth import require_api_key
from agent_on_demand.models import APIKey

User = get_user_model()


def body(resp):
    return json.loads(resp.content)


def call_async(view, request):
    """Invoke an async view from a sync test. Needed because mutmut runs tests
    via hammett, which doesn't load pytest-asyncio or pytest-django plugins."""
    return async_to_sync(view)(request)


@pytest.fixture
def user(db):
    return User.objects.create_user(username="authtestuser")


@pytest.fixture
def api_key(user):
    return APIKey.create_key(user, "test-key")


@pytest.fixture
def factory():
    return RequestFactory()


def _ok_response(request):
    return JsonResponse({"user_id": request.user.id, "key_id": request.api_key_obj.id, "ok": True})


def _make_sync_view():
    return require_api_key(_ok_response)


def _make_async_view():
    async def _async_ok(request):
        return _ok_response(request)

    return require_api_key(_async_ok)


# === Missing/invalid header (sync) ===


def test_missing_authorization_header(factory):
    resp = _make_sync_view()(factory.get("/"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "Missing or invalid Authorization header"}


def test_non_bearer_scheme(factory):
    resp = _make_sync_view()(factory.get("/", HTTP_AUTHORIZATION="Token abc"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "Missing or invalid Authorization header"}


def test_bearer_with_lowercase_scheme(factory):
    """Authorization scheme is case-sensitive — 'bearer' (lowercase) must be rejected."""
    resp = _make_sync_view()(factory.get("/", HTTP_AUTHORIZATION="bearer abc"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "Missing or invalid Authorization header"}


def test_invalid_api_key_exact_response(factory, db):
    resp = _make_sync_view()(factory.get("/", HTTP_AUTHORIZATION="Bearer aod_does_not_exist"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "Invalid API key"}


# === Inactive key (sync) ===


def test_inactive_key_exact_response(factory, api_key):
    instance, raw = api_key
    instance.is_active = False
    instance.save()
    resp = _make_sync_view()(factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "API key is inactive"}


# === Expiry (sync) ===


def test_expired_key_exact_response(factory, api_key):
    instance, raw = api_key
    instance.expires_at = tz.now() - timedelta(seconds=1)
    instance.save()
    resp = _make_sync_view()(factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "API key has expired"}


def test_expired_key_at_exact_now_rejected(factory, api_key, monkeypatch):
    """Boundary: expires_at == now must be treated as expired (uses <=, not <)."""
    instance, raw = api_key
    fixed = tz.now()
    instance.expires_at = fixed
    instance.save()
    monkeypatch.setattr("agent_on_demand.auth.tz.now", lambda: fixed)
    resp = _make_sync_view()(factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "API key has expired"}


def test_unexpired_key_allowed(factory, api_key):
    instance, raw = api_key
    instance.expires_at = tz.now() + timedelta(hours=1)
    instance.save()
    resp = _make_sync_view()(factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 200


def test_no_expiry_allowed(factory, api_key):
    """expires_at=None means key never expires."""
    _, raw = api_key
    resp = _make_sync_view()(factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 200


# === Success path (sync) ===


def test_valid_key_attaches_user_and_key_obj(factory, user, api_key):
    instance, raw = api_key
    resp = _make_sync_view()(factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 200
    payload = body(resp)
    assert payload["user_id"] == user.id
    assert payload["key_id"] == instance.id
    assert payload["ok"] is True


# === Async wrapper (called sync via async_to_sync; see hammett note above) ===


def test_async_missing_authorization_header(factory):
    resp = call_async(_make_async_view(), factory.get("/"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "Missing or invalid Authorization header"}


def test_async_non_bearer_scheme(factory):
    resp = call_async(_make_async_view(), factory.get("/", HTTP_AUTHORIZATION="Token abc"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "Missing or invalid Authorization header"}


def test_async_invalid_key_exact_response(factory, db):
    resp = call_async(
        _make_async_view(),
        factory.get("/", HTTP_AUTHORIZATION="Bearer aod_no_such"),
    )
    assert resp.status_code == 401
    assert body(resp) == {"detail": "Invalid API key"}


def test_async_inactive_key_exact_response(factory, api_key):
    instance, raw = api_key
    instance.is_active = False
    instance.save()
    resp = call_async(_make_async_view(), factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "API key is inactive"}


def test_async_expired_key_exact_response(factory, api_key):
    instance, raw = api_key
    instance.expires_at = tz.now() - timedelta(seconds=1)
    instance.save()
    resp = call_async(_make_async_view(), factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "API key has expired"}


def test_async_expired_key_at_exact_now_rejected(factory, api_key, monkeypatch):
    instance, raw = api_key
    fixed = tz.now()
    instance.expires_at = fixed
    instance.save()
    monkeypatch.setattr("agent_on_demand.auth.tz.now", lambda: fixed)
    resp = call_async(_make_async_view(), factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 401
    assert body(resp) == {"detail": "API key has expired"}


def test_async_valid_key_attaches_user(factory, user, api_key):
    instance, raw = api_key
    resp = call_async(_make_async_view(), factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}"))
    assert resp.status_code == 200
    payload = body(resp)
    assert payload["user_id"] == user.id
    assert payload["key_id"] == instance.id


# === select_related("user") pin (auth N+1 guard) ===
#
# `_check_api_key_{sync,async}` use `APIKey.objects.select_related("user").get(...)`
# so `request.user = api_key.user` in the wrapper doesn't trigger a second
# query. Drop the `select_related("user")` and the auth check goes from one
# query to two — these tests fail in that case, killing the mutation that
# replaces the join hint with `None`.


def test_sync_auth_select_related_keeps_user_load_to_one_query(factory, api_key):
    _, raw = api_key
    request = factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}")
    with CaptureQueriesContext(connection) as ctx:
        resp = _make_sync_view()(request)
    assert resp.status_code == 200
    assert len(ctx.captured_queries) == 1, (
        f"expected 1 query (select_related join), got {len(ctx.captured_queries)}: "
        f"{[q['sql'] for q in ctx.captured_queries]}"
    )


def test_async_auth_select_related_keeps_user_load_to_one_query(factory, api_key):
    _, raw = api_key
    request = factory.get("/", HTTP_AUTHORIZATION=f"Bearer {raw}")
    with CaptureQueriesContext(connection) as ctx:
        resp = call_async(_make_async_view(), request)
    assert resp.status_code == 200
    assert len(ctx.captured_queries) == 1, (
        f"expected 1 query (select_related join), got {len(ctx.captured_queries)}: "
        f"{[q['sql'] for q in ctx.captured_queries]}"
    )
