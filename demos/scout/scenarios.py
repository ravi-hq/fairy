"""
Scout demo scenarios — pre-loaded mock alert scenarios with investigation outputs.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class Alert:
    id: str
    priority: str          # P1 / P2 / P3
    service: str
    name: str
    description: str
    scenario_key: str


@dataclass
class Brief:
    timestamp: str
    affected_surface: str
    probable_cause: str
    evidence: List[str]
    blast_radius: str
    suggested_action: str


@dataclass
class Scenario:
    alert: Alert
    investigation_steps: List[str]
    brief: Brief


# ---------------------------------------------------------------------------
# Scenario 1 — Payment error rate spike
# ---------------------------------------------------------------------------
SCENARIO_PAYMENT = Scenario(
    alert=Alert(
        id="alert-001",
        priority="P1",
        service="checkout-service",
        name="Error rate spike on /api/checkout",
        description="Error rate exceeded 500/min threshold (current: 847/min)",
        scenario_key="payment",
    ),
    investigation_steps=[
        "[scout] Waking up — alert received from checkout-service",
        "[metrics] Querying error rate for /api/checkout (window: 10 min)...",
        "[metrics] Found: 847 errors/min at 02:43:17 UTC  (baseline: 12/min) ▲ 6958%",
        "[deploys] Fetching recent deployments for checkout-service...",
        "[deploys] Found: v2.4.1 deployed at 02:40:33 UTC (3 min before spike)",
        "[deploys] Diff summary: payment_retry_v2 feature flag enabled, retry backoff changed",
        "[traces] Sampling affected requests from 02:40:00–02:44:00 UTC...",
        "[traces] 12.4% of /api/checkout requests returning HTTP 503",
        "[traces] Downstream call: payments-service → 503 (avg latency 8.4 s)",
        "[logs] Tailing payments-service logs for connection pool errors...",
        "[logs] ERROR pool exhausted: no available connections (pool_size=20, waiting=140)",
        "[logs] Pattern: pool exhaustion starts at 02:40:35 UTC — 2 s after deploy",
        "[hypothesis] payment_retry_v2 adds aggressive retry on transient failure;",
        "[hypothesis] each checkout attempt holds a DB connection for the retry window,",
        "[hypothesis] exhausting the pool of 20 under normal checkout volume.",
        "[scout] Investigation complete — drafting brief...",
    ],
    brief=Brief(
        timestamp="02:43:17 UTC",
        affected_surface="/api/checkout → payments-service → payments DB pool",
        probable_cause=(
            "v2.4.1 enabled payment_retry_v2 flag, introducing aggressive retry logic "
            "that holds DB connections during retry backoff windows, exhausting the "
            "connection pool (size=20) under normal traffic."
        ),
        evidence=[
            "847 errors/min at 02:43:17 UTC vs baseline 12/min",
            "v2.4.1 deployed 3 minutes before spike at 02:40:33 UTC",
            "payments-service pool exhaustion errors begin at 02:40:35 UTC",
            "503s confined to /api/checkout; other endpoints unaffected",
        ],
        blast_radius="12.4% of checkout requests failing — estimated $4,200/min revenue impact",
        suggested_action=(
            "Immediate: toggle off payment_retry_v2 feature flag (zero-downtime). "
            "If flag not accessible, rollback to v2.4.0. "
            "Follow-up: increase pool size to 50 before re-enabling flag."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Scenario 2 — Database latency / slow query
# ---------------------------------------------------------------------------
SCENARIO_DB = Scenario(
    alert=Alert(
        id="alert-002",
        priority="P2",
        service="feed-service",
        name="p99 latency > 2000 ms on /api/feed",
        description="p99 response time is 4,318 ms — SLO threshold is 2,000 ms",
        scenario_key="db",
    ),
    investigation_steps=[
        "[scout] Waking up — alert received from feed-service",
        "[metrics] Querying p99 latency for /api/feed (window: 15 min)...",
        "[metrics] Found: p99 = 4,318 ms at 09:17:02 UTC  (SLO: 2,000 ms)",
        "[metrics] p50 = 420 ms — spread suggests tail latency, not full degradation",
        "[db] Checking slow query log on feeds-primary (last 20 min)...",
        "[db] Found 1,847 slow queries (>1 s) on table: user_posts",
        "[db] Most common query: SELECT * FROM user_posts WHERE user_id=? ORDER BY created_at DESC",
        "[db] Avg execution time: 3,940 ms — previously <10 ms",
        "[db] Running EXPLAIN on slow query...",
        "[db] EXPLAIN: Seq Scan on user_posts (rows=2,847,203) — no index used",
        "[migrations] Fetching recent DB migrations...",
        "[migrations] migration 0047 ran at 09:12:44 UTC: 'drop_redundant_indexes'",
        "[migrations] migration 0047 dropped: idx_user_posts_user_id_created_at",
        "[hypothesis] migration 0047 dropped the composite index used by the feed query.",
        "[hypothesis] Without the index, every feed load does a full sequential scan",
        "[hypothesis] of 2.8 M rows — explaining 3.9 s avg query time.",
        "[scout] Investigation complete — drafting brief...",
    ],
    brief=Brief(
        timestamp="09:17:02 UTC",
        affected_surface="/api/feed → feeds-primary DB → user_posts table",
        probable_cause=(
            "Migration 0047 ('drop_redundant_indexes') dropped idx_user_posts_user_id_created_at. "
            "The primary feed query relies on this index; without it, Postgres falls back to a "
            "sequential scan of 2.8 M rows, adding ~3.9 s per request."
        ),
        evidence=[
            "p99 latency 4,318 ms at 09:17:02 UTC (SLO: 2,000 ms)",
            "1,847 slow queries on user_posts in the last 20 minutes",
            "EXPLAIN shows Seq Scan — no index used",
            "migration 0047 ran at 09:12:44 UTC, 4 minutes before alert",
        ],
        blast_radius=(
            "All /api/feed requests degraded; p50 still 420 ms — heavy users (~top 5%) "
            "with large follow graphs experience full timeout (>5 s)."
        ),
        suggested_action=(
            "Immediate: run CREATE INDEX CONCURRENTLY idx_user_posts_user_id_created_at "
            "ON user_posts(user_id, created_at DESC) — non-blocking, resolves in ~8 min. "
            "Follow-up: add index inventory check to migration CI pipeline."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Scenario 3 — Worker pod OOMKilled (memory leak)
# ---------------------------------------------------------------------------
SCENARIO_MEMORY = Scenario(
    alert=Alert(
        id="alert-003",
        priority="P2",
        service="worker-service",
        name="Worker pod OOMKilled (3× in 10 min)",
        description="worker-6b7d9f-xk2p killed by OOM killer — 3 restarts in 10 minutes",
        scenario_key="memory",
    ),
    investigation_steps=[
        "[scout] Waking up — alert received from worker-service",
        "[k8s] Querying pod events for worker-service (namespace: production)...",
        "[k8s] worker-6b7d9f-xk2p: OOMKilled at 14:02:11, 14:05:44, 14:09:17 UTC",
        "[k8s] Pod memory limit: 512 Mi — RSS at kill time: 511 Mi each time",
        "[metrics] Plotting RSS growth for worker pods (last 2 h)...",
        "[metrics] worker-6b7d9f-xk2p: linear growth from 180 Mi → 512 Mi over 95 min",
        "[metrics] Other pods (worker-6b7d9f-zp4q, etc.): stable at ~190 Mi",
        "[metrics] Growth rate: ~3.5 Mi/min starting at 12:27:00 UTC",
        "[deploys] Checking deploys around 12:27 UTC...",
        "[deploys] v1.9.3 deployed at 12:25:18 UTC — changelog: 'add live event SSE endpoint'",
        "[code] Reviewing background task added in v1.9.3...",
        "[code] Found: async def stream_events() opens SSEResponse per client connection",
        "[code] Found: connections stored in module-level set `_live_connections`",
        "[code] Found: no cleanup on client disconnect — generator not closed on CancelledError",
        "[code] Each dead connection leaks: response buffer (≈1.2 Mi) + generator frame",
        "[metrics] Checking SSE connection counts...",
        "[metrics] Connections accepted: 2,847 since 12:25 UTC — closed cleanly: 31",
        "[hypothesis] stream_events() does not handle client disconnect:",
        "[hypothesis] each dropped browser tab / mobile app background leaves a live generator",
        "[hypothesis] holding 1.2 Mi of buffer. 2,816 leaked connections = ~3.4 Gi total",
        "[hypothesis] — pod hits 512 Mi limit and is killed every ~3.5 minutes.",
        "[scout] Investigation complete — drafting brief...",
    ],
    brief=Brief(
        timestamp="14:02:11 UTC (first kill)",
        affected_surface="worker-service pod worker-6b7d9f-xk2p → /api/events SSE endpoint",
        probable_cause=(
            "v1.9.3 introduced a live-event SSE endpoint whose background generator is never "
            "cancelled on client disconnect. Each disconnected client leaves a live asyncio "
            "generator holding ~1.2 Mi of response buffer in a module-level set. "
            "2,816 leaked connections accumulate ~3.4 Gi — pod hits 512 Mi limit and is "
            "OOMKilled every ~3.5 minutes."
        ),
        evidence=[
            "OOMKilled 3× (14:02, 14:05, 14:09 UTC) — other pods stable",
            "Linear RSS growth: 3.5 Mi/min from 12:27 UTC — matches v1.9.3 deploy",
            "2,847 SSE connections accepted; only 31 closed cleanly",
            "Code review: no CancelledError / disconnect handler in stream_events()",
        ],
        blast_radius=(
            "Single pod crashing; Kubernetes restarts it within 30 s each time. "
            "Live-event feed is unavailable during restart window (~30 s every 3.5 min). "
            "Other services unaffected."
        ),
        suggested_action=(
            "Immediate: rollback to v1.9.2 to stop leak accumulation. "
            "Fix: add try/finally in stream_events() to remove connection from "
            "_live_connections on CancelledError or StopAsyncIteration. "
            "Also add memory-based circuit breaker to reject new SSE connections above 400 Mi RSS."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
SCENARIOS = {
    "payment": SCENARIO_PAYMENT,
    "db": SCENARIO_DB,
    "memory": SCENARIO_MEMORY,
}

SCENARIO_ORDER = ["payment", "db", "memory"]
