"""Optimistic-concurrency helpers for versioned resources (agents, environments).

The version-mismatch response shape — `{"detail": "Version mismatch: expected
N, got M"}` with status 409 — is part of the API contract. Clients parse the
expected vs got values to decide whether to retry. Don't change the format
without coordinating with SDK consumers; tests in tests/test_versioning.py
pin the exact wording.
"""

from __future__ import annotations

from django.http import JsonResponse


def check_version_match(request_version: int, current_version: int) -> JsonResponse | None:
    """Return a 409 response if the requested version doesn't match the current
    version, or None if it matches and the caller should proceed.
    """
    if request_version != current_version:
        return JsonResponse(
            {"detail": f"Version mismatch: expected {current_version}, got {request_version}"},
            status=409,
        )
    return None
