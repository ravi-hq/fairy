"""Direct tests for the optimistic-concurrency check_version_match helper.

Pin the Version-mismatch response shape — clients (SDKs) depend on it to
detect retryable conflicts.
"""

import json

from agent_on_demand.versioning import check_version_match


def body(resp):
    return json.loads(resp.content)


def test_matching_versions_returns_none():
    assert check_version_match(1, 1) is None


def test_matching_zero_versions_returns_none():
    assert check_version_match(0, 0) is None


def test_matching_large_versions_returns_none():
    assert check_version_match(2_000_000_000, 2_000_000_000) is None


def test_mismatch_returns_409():
    resp = check_version_match(1, 2)
    assert resp is not None
    assert resp.status_code == 409


def test_mismatch_response_shape_exact():
    """The detail string format is part of the API contract — SDKs parse it."""
    resp = check_version_match(3, 7)
    assert body(resp) == {"detail": "Version mismatch: expected 7, got 3"}


def test_mismatch_response_only_has_detail_key():
    """No extra fields in the error response."""
    resp = check_version_match(1, 2)
    payload = body(resp)
    assert set(payload.keys()) == {"detail"}


def test_mismatch_lower_request_version():
    """Client sent stale version — response should report it correctly."""
    resp = check_version_match(1, 5)
    assert body(resp) == {"detail": "Version mismatch: expected 5, got 1"}


def test_mismatch_higher_request_version():
    """Client somehow sent a future version — response should report it correctly."""
    resp = check_version_match(99, 5)
    assert body(resp) == {"detail": "Version mismatch: expected 5, got 99"}


def test_mismatch_against_zero_current():
    resp = check_version_match(1, 0)
    assert body(resp) == {"detail": "Version mismatch: expected 0, got 1"}


def test_mismatch_against_zero_request():
    resp = check_version_match(0, 1)
    assert body(resp) == {"detail": "Version mismatch: expected 1, got 0"}


def test_mismatch_off_by_one():
    """The most common real-world conflict: client retried with the version
    just before the one that was concurrently bumped."""
    resp = check_version_match(4, 5)
    assert resp.status_code == 409
    assert body(resp) == {"detail": "Version mismatch: expected 5, got 4"}
