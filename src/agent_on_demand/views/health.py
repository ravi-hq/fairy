"""Health check exercised by Render's auto-rollback path.

Verifies enough that a broken deploy gets rolled back automatically: DB is
reachable + queryable, and field encryption round-trips. Returns 503 (not
200) when a check fails so Render's healthCheckPath catches it.

Render polls this every ~5s; both checks are O(1) and the total budget is
well under the default 5s health-check timeout.
"""

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from agent_on_demand.crypto import decrypt, encrypt


def _check_db() -> str:
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return "ok"
    except Exception as e:
        return f"fail: {e.__class__.__name__}"


def _check_crypto() -> str:
    try:
        if decrypt(encrypt("ping")) == "ping":
            return "ok"
        return "fail: round-trip mismatch"
    except Exception as e:
        return f"fail: {e.__class__.__name__}"


@require_GET
def health(request):
    checks = {
        "db": _check_db(),
        "crypto": _check_crypto(),
    }
    all_ok = all(v == "ok" for v in checks.values())
    return JsonResponse(
        {"status": "ok" if all_ok else "degraded", "checks": checks},
        status=200 if all_ok else 503,
    )
