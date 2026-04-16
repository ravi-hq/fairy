import json

import pytest
from django.test import Client


@pytest.mark.django_db
def test_health(client: Client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.django_db
def test_health_rejects_post(client: Client):
    resp = client.post("/health")
    assert resp.status_code == 405


@pytest.mark.django_db
def test_run_invalid_json(client: Client):
    resp = client.post("/run", data="not json", content_type="application/json")
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_missing_fields(client: Client):
    resp = client.post(
        "/run",
        data=json.dumps({"runtime": "claude"}),
        content_type="application/json",
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_invalid_runtime(client: Client):
    resp = client.post(
        "/run",
        data=json.dumps({"runtime": "invalid", "prompt": "hello", "api_key": "fake"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "Unknown runtime" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_timeout_too_low(client: Client):
    resp = client.post(
        "/run",
        data=json.dumps({"runtime": "claude", "prompt": "hello", "api_key": "fake", "timeout": 5}),
        content_type="application/json",
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_timeout_too_high(client: Client):
    resp = client.post(
        "/run",
        data=json.dumps(
            {"runtime": "claude", "prompt": "hello", "api_key": "fake", "timeout": 9999}
        ),
        content_type="application/json",
    )
    assert resp.status_code == 422
