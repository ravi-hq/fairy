"""Tests for /health — Render's auto-rollback signal.

The endpoint must:
  - return 200 + status=ok when DB and crypto round-trip both work
  - return 503 + per-check detail when either fails

These pin the contract Render relies on; a regression here disables
auto-rollback for downstream deploys.
"""

import json

from django.test import Client


def body(resp):
    return json.loads(resp.content)


def test_health_happy_path(client: Client, db):
    resp = client.get("/health")
    assert resp.status_code == 200
    payload = body(resp)
    assert payload == {"status": "ok", "checks": {"db": "ok", "crypto": "ok"}}


def test_health_503_when_db_check_fails(client: Client, db, monkeypatch):
    """A DB failure must surface as 503 so Render rolls back."""

    def raise_oserror():
        raise OSError("simulated DB outage")

    # Patch the helper directly — simpler than mocking django.db.connection.
    monkeypatch.setattr("agent_on_demand.views.health._check_db", lambda: "fail: OSError")

    resp = client.get("/health")
    assert resp.status_code == 503
    payload = body(resp)
    assert payload["status"] == "degraded"
    assert payload["checks"]["db"].startswith("fail")
    assert payload["checks"]["crypto"] == "ok"


def test_health_503_when_crypto_check_fails(client: Client, db, monkeypatch):
    """A crypto failure (e.g. FIELD_ENCRYPTION_KEY misconfig) must surface as 503."""
    monkeypatch.setattr(
        "agent_on_demand.views.health._check_crypto",
        lambda: "fail: InvalidToken",
    )

    resp = client.get("/health")
    assert resp.status_code == 503
    payload = body(resp)
    assert payload["status"] == "degraded"
    assert payload["checks"]["db"] == "ok"
    assert payload["checks"]["crypto"].startswith("fail")


def test_health_503_when_both_fail(client: Client, db, monkeypatch):
    monkeypatch.setattr("agent_on_demand.views.health._check_db", lambda: "fail: A")
    monkeypatch.setattr("agent_on_demand.views.health._check_crypto", lambda: "fail: B")

    resp = client.get("/health")
    assert resp.status_code == 503
    payload = body(resp)
    assert payload == {
        "status": "degraded",
        "checks": {"db": "fail: A", "crypto": "fail: B"},
    }


def test_health_response_keys_are_stable(client: Client, db):
    """Outer keys are part of the contract for whatever's parsing /health
    (Checkly external probe, Render auto-rollback). Pin them so a refactor
    can't silently rename them."""
    resp = client.get("/health")
    payload = body(resp)
    assert set(payload.keys()) == {"status", "checks"}
    assert set(payload["checks"].keys()) == {"db", "crypto"}


def test_health_does_not_require_auth(client: Client, db):
    """Render and Checkly probe /health unauthenticated — adding auth here
    would break auto-rollback and external monitoring."""
    resp = client.get("/health")
    assert resp.status_code == 200
    # No Authorization header sent.
    assert resp.headers.get("WWW-Authenticate") is None
