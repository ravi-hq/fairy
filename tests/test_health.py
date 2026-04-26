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


# The tests above monkey-patch `_check_db` / `_check_crypto` to short-circuit
# the failure response, which means the *actual* exception-handling branches
# never execute. The tests below exercise those branches with real raises so
# a future refactor that narrows `except Exception` to a specific class can't
# silently slip past CI.


def test_check_db_returns_failure_string_on_real_exception(db, monkeypatch):
    """The bare `except Exception` in _check_db must catch arbitrary errors
    and surface the class name. If a refactor narrows that catch and the DB
    raises an unexpected class, /health would 500 instead of returning 503,
    defeating Render auto-rollback."""

    from agent_on_demand.views import health

    class _BoomConnection:
        def cursor(self):
            raise RuntimeError("simulated outage")

    monkeypatch.setattr(health, "connection", _BoomConnection())
    assert health._check_db() == "fail: RuntimeError"


def test_check_crypto_returns_round_trip_mismatch_when_decrypt_differs(monkeypatch):
    """If encrypt/decrypt are wired up but the round-trip yields a different
    plaintext (key rotation gone wrong, wrong cipher selected), the helper
    must report 'round-trip mismatch' — a distinct signal from a hard raise."""

    from agent_on_demand.views import health

    monkeypatch.setattr(health, "encrypt", lambda s: b"opaque-bytes")
    monkeypatch.setattr(health, "decrypt", lambda b: "not-ping")
    assert health._check_crypto() == "fail: round-trip mismatch"


def test_check_crypto_returns_failure_string_on_real_exception(monkeypatch):
    """An exception thrown by encrypt or decrypt (e.g. InvalidToken from a
    rotated FIELD_ENCRYPTION_KEY) must be caught and surfaced via class
    name — same contract as _check_db."""

    from agent_on_demand.views import health

    def _boom(_):
        raise ValueError("simulated cipher failure")

    monkeypatch.setattr(health, "encrypt", _boom)
    assert health._check_crypto() == "fail: ValueError"
