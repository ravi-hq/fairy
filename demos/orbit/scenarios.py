"""
Orbit demo scenarios — pre-loaded missions and auto-generated knowledge-base docs.

Orbit reimagines Atlassian (Jira + Confluence) for a world where work is split
between agents and humans. The atomic unit is a *mission* — an outcome — that
is decomposed into a tree of work items dispatched to agents, humans, or both.
"""

from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: str
    title: str
    assignee: str            # "Researcher", "Spec Drafter", "PR Bot", "Scout", "@maya", ...
    assignee_kind: str       # "agent" | "human"
    status: str              # "queued" | "running" | "awaiting" | "done" | "blocked"
    detail: Optional[str] = None             # short subline (e.g. "currently doing")
    artifact_label: Optional[str] = None     # "PR #1234", "Brief", "Decision Log"
    artifact_kind: Optional[str] = None      # "pr" | "brief" | "doc"
    cost_usd: float = 0.0                    # rough $ for the task (agent compute)
    duration_min: int = 0                    # minutes spent (agent or human)


@dataclass
class ActivityEvent:
    timestamp: str           # human-readable relative ("4m ago")
    text: str
    kind: str = "info"       # "info" | "agent" | "human" | "approval"


@dataclass
class Mission:
    id: int
    title: str
    status: str              # "in-flight" | "done" | "blocked" | "drafting"
    owner: str
    owner_initials: str
    started: str
    target: str
    outcome: str
    tasks: List[Task]
    activity: List[ActivityEvent]
    last_activity: str

    @property
    def progress(self) -> str:
        done = sum(1 for t in self.tasks if t.status == "done")
        return f"{done}/{len(self.tasks)} tasks done"

    @property
    def progress_pct(self) -> int:
        if not self.tasks:
            return 0
        done = sum(1 for t in self.tasks if t.status == "done")
        return int(round(100 * done / len(self.tasks)))


@dataclass
class KbDoc:
    id: int
    title: str
    category: str            # "postmortem" | "decision" | "digest" | "spec"
    source_mission_id: int
    source_mission_title: str
    generated_at: str        # human-readable
    event_count: int
    body: str                # markdown


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------

MISSION_1 = Mission(
    id=1,
    title="Ship v2.4 of the checkout flow",
    status="in-flight",
    owner="Maya",
    owner_initials="MA",
    started="3d ago",
    target="Friday",
    outcome=(
        "Cut checkout error rate in EU regions by introducing retry-with-backoff "
        "on transient payment failures, and ship behind a 10% canary."
    ),
    tasks=[
        Task(
            id="m1-t1",
            title="Audit current checkout error rates by region",
            assignee="Researcher",
            assignee_kind="agent",
            status="done",
            artifact_label="Brief",
            artifact_kind="brief",
            cost_usd=1.42,
            duration_min=18,
        ),
        Task(
            id="m1-t2",
            title="Draft spec for retry-with-backoff",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="done",
            artifact_label="Spec doc",
            artifact_kind="doc",
            cost_usd=2.18,
            duration_min=24,
        ),
        Task(
            id="m1-t3",
            title="Open PR with retry implementation",
            assignee="PR Bot",
            assignee_kind="agent",
            status="done",
            artifact_label="PR #1234",
            artifact_kind="pr",
            cost_usd=3.74,
            duration_min=41,
        ),
        Task(
            id="m1-t4",
            title="Review PR #1234",
            assignee="@maya",
            assignee_kind="human",
            status="done",
            cost_usd=0.0,
            duration_min=22,
        ),
        Task(
            id="m1-t5",
            title="Run regression suite against staging",
            assignee="Scout",
            assignee_kind="agent",
            status="running",
            detail="running test 41/120: payment_retry_v2_e2e",
            cost_usd=0.84,
            duration_min=12,
        ),
        Task(
            id="m1-t6",
            title="Approve production rollout",
            assignee="@jake",
            assignee_kind="human",
            status="blocked",
            detail="Waiting on regression suite + canary metrics",
            cost_usd=0.0,
            duration_min=0,
        ),
    ],
    activity=[
        ActivityEvent("just now", "Scout: running test 41/120: payment_retry_v2_e2e", kind="agent"),
        ActivityEvent("4m ago", "Scout started regression suite (120 tests)", kind="agent"),
        ActivityEvent("1h ago", "@maya approved PR #1234", kind="approval"),
        ActivityEvent("1h ago", "PR Bot opened PR #1234 (retry-with-backoff impl)", kind="agent"),
        ActivityEvent("4h ago", "Spec Drafter: posted spec for retry-with-backoff", kind="agent"),
        ActivityEvent("yesterday", "Researcher: finished checkout error-rate audit (4 regions)", kind="agent"),
        ActivityEvent("3d ago", "Maya started mission", kind="human"),
    ],
    last_activity="just now",
)

MISSION_2 = Mission(
    id=2,
    title="Investigate weekend latency spike",
    status="done",
    owner="Sam",
    owner_initials="SA",
    started="3d ago",
    target="Closed Mon",
    outcome=(
        "Identify root cause of the Saturday 02:00–04:00 UTC p99 latency spike "
        "on /api/feed and ship a mitigation before next weekend."
    ),
    tasks=[
        Task(
            id="m2-t1",
            title="Investigate root cause",
            assignee="Scout",
            assignee_kind="agent",
            status="done",
            artifact_label="Investigation log",
            artifact_kind="doc",
            cost_usd=2.92,
            duration_min=7,
        ),
        Task(
            id="m2-t2",
            title="Compare with last 4 weekends' traffic",
            assignee="Researcher",
            assignee_kind="agent",
            status="done",
            artifact_label="Brief",
            artifact_kind="brief",
            cost_usd=1.85,
            duration_min=14,
        ),
        Task(
            id="m2-t3",
            title="Draft remediation plan",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="done",
            artifact_label="Plan doc",
            artifact_kind="doc",
            cost_usd=2.36,
            duration_min=21,
        ),
        Task(
            id="m2-t4",
            title="Approve mitigation",
            assignee="@sam",
            assignee_kind="human",
            status="done",
            cost_usd=0.0,
            duration_min=18,
        ),
    ],
    activity=[
        ActivityEvent("2h ago", "Postmortem auto-written and filed in Knowledge Base", kind="info"),
        ActivityEvent("3h ago", "@sam approved mitigation plan — pinning feed-cache TTL to 90s", kind="approval"),
        ActivityEvent("5h ago", "Spec Drafter: remediation plan ready for review", kind="agent"),
        ActivityEvent("yesterday", "Researcher: weekend traffic baseline complete (4 weeks)", kind="agent"),
        ActivityEvent("yesterday", "Scout: root cause = cache stampede on feed-cache restart", kind="agent"),
        ActivityEvent("3d ago", "Sam started mission from PagerDuty alert", kind="human"),
    ],
    last_activity="2h ago",
)

MISSION_3 = Mission(
    id=3,
    title="Decide on payment provider for EU rollout",
    status="in-flight",
    owner="Jake",
    owner_initials="JA",
    started="6d ago",
    target="Next Wed",
    outcome=(
        "Pick a payment provider that supports SEPA + iDEAL + Bancontact at <2.4% "
        "blended cost, with a clear migration plan from our current US-only stack."
    ),
    tasks=[
        Task(
            id="m3-t1",
            title="Survey 4 candidate providers",
            assignee="Researcher",
            assignee_kind="agent",
            status="done",
            artifact_label="Brief",
            artifact_kind="brief",
            cost_usd=4.18,
            duration_min=52,
        ),
        Task(
            id="m3-t2",
            title="Build cost-model spreadsheet",
            assignee="Researcher",
            assignee_kind="agent",
            status="done",
            artifact_label="Cost model",
            artifact_kind="doc",
            cost_usd=3.27,
            duration_min=38,
        ),
        Task(
            id="m3-t3",
            title="Compile decision memo with tradeoffs",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="done",
            artifact_label="Decision Log",
            artifact_kind="doc",
            cost_usd=2.95,
            duration_min=29,
        ),
        Task(
            id="m3-t4",
            title="Schedule call with finance",
            assignee="@maya",
            assignee_kind="human",
            status="awaiting",
            detail="Two slots offered — awaiting finance reply",
            cost_usd=0.0,
            duration_min=8,
        ),
        Task(
            id="m3-t5",
            title="Pick provider",
            assignee="@jake",
            assignee_kind="human",
            status="awaiting",
            detail="Decision memo open in Knowledge Base",
            cost_usd=0.0,
            duration_min=0,
        ),
    ],
    activity=[
        ActivityEvent("6h ago", "Spec Drafter: decision memo posted to Knowledge Base", kind="agent"),
        ActivityEvent("yesterday", "Researcher: cost model finalized (Adyen wins at $1.98M/yr)", kind="agent"),
        ActivityEvent("2d ago", "Researcher: provider survey complete (Stripe, Adyen, Mollie, Checkout.com)", kind="agent"),
        ActivityEvent("6d ago", "Jake started mission", kind="human"),
    ],
    last_activity="6h ago",
)

MISSION_4 = Mission(
    id=4,
    title="Onboard the new platform-eng team",
    status="drafting",
    owner="Maya",
    owner_initials="MA",
    started="1h ago",
    target="Next Mon",
    outcome=(
        "Get the four new platform-eng hires productive: production access, "
        "on-call shadow rotation, and an onboarding doc that points at real recent work."
    ),
    tasks=[
        Task(
            id="m4-t1",
            title="Pull recent platform issues from incident DB",
            assignee="Researcher",
            assignee_kind="agent",
            status="queued",
            cost_usd=0.0,
            duration_min=0,
        ),
        Task(
            id="m4-t2",
            title="Draft onboarding doc skeleton",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="queued",
            cost_usd=0.0,
            duration_min=0,
        ),
    ],
    activity=[
        ActivityEvent("1h ago", "Maya created mission — tasks queued, not yet dispatched", kind="human"),
    ],
    last_activity="1h ago",
)

MISSION_5 = Mission(
    id=5,
    title="Migrate user notifications to v3 schema",
    status="in-flight",
    owner="Sam",
    owner_initials="SA",
    started="5d ago",
    target="Next Tue",
    outcome=(
        "Roll the notifications table forward to the v3 schema (typed payload, "
        "fan-out indexed by user_id+channel) without dropping any in-flight "
        "deliveries, behind a dual-write fence."
    ),
    tasks=[
        Task(
            id="m5-t1",
            title="Audit current notifications schema usage",
            assignee="Researcher",
            assignee_kind="agent",
            status="done",
            artifact_label="Audit brief",
            artifact_kind="brief",
            cost_usd=2.04,
            duration_min=22,
        ),
        Task(
            id="m5-t2",
            title="Draft dual-write migration plan",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="done",
            artifact_label="Migration plan",
            artifact_kind="doc",
            cost_usd=2.66,
            duration_min=31,
        ),
        Task(
            id="m5-t3",
            title="Generate forward + backfill migrations",
            assignee="Migrator",
            assignee_kind="agent",
            status="done",
            artifact_label="PR #1290",
            artifact_kind="pr",
            cost_usd=4.38,
            duration_min=46,
        ),
        Task(
            id="m5-t4",
            title="Dry-run backfill against staging snapshot",
            assignee="Scout",
            assignee_kind="agent",
            status="done",
            artifact_label="Dry-run report",
            artifact_kind="doc",
            cost_usd=1.79,
            duration_min=15,
        ),
        Task(
            id="m5-t5",
            title="Approve forward migration",
            assignee="@sam",
            assignee_kind="human",
            status="awaiting",
            detail="Migrator surfaced one ambiguous column — needs a human call",
            cost_usd=0.0,
            duration_min=0,
        ),
        Task(
            id="m5-t6",
            title="Run forward migration in prod",
            assignee="Migrator",
            assignee_kind="agent",
            status="queued",
            cost_usd=0.0,
            duration_min=0,
        ),
        Task(
            id="m5-t7",
            title="Cut readers over to v3, drop dual-write",
            assignee="PR Bot",
            assignee_kind="agent",
            status="queued",
            cost_usd=0.0,
            duration_min=0,
        ),
    ],
    activity=[
        ActivityEvent("30m ago", "Migrator: surfaced ambiguous column for human review", kind="agent"),
        ActivityEvent("2h ago", "Scout: dry-run backfill clean against staging snapshot", kind="agent"),
        ActivityEvent("yesterday", "Migrator: opened PR #1290 (forward + backfill migrations)", kind="agent"),
        ActivityEvent("2d ago", "Spec Drafter: dual-write plan posted", kind="agent"),
        ActivityEvent("3d ago", "Researcher: schema audit complete (8 callsites)", kind="agent"),
        ActivityEvent("5d ago", "Sam started mission", kind="human"),
    ],
    last_activity="30m ago",
)

MISSION_6 = Mission(
    id=6,
    title="Q2 capacity planning",
    status="drafting",
    owner="Sam",
    owner_initials="SA",
    started="2h ago",
    target="2 weeks",
    outcome=(
        "Produce a Q2 capacity plan: headcount asks per team, infra budget "
        "deltas, and a list of bets we're explicitly *not* funding."
    ),
    tasks=[
        Task(
            id="m6-t1",
            title="Pull last 6 weeks of utilization data per team",
            assignee="Researcher",
            assignee_kind="agent",
            status="queued",
            cost_usd=0.0,
            duration_min=0,
        ),
        Task(
            id="m6-t2",
            title="Survey each team lead on their top 2 Q2 bets",
            assignee="Researcher",
            assignee_kind="agent",
            status="queued",
            cost_usd=0.0,
            duration_min=0,
        ),
        Task(
            id="m6-t3",
            title="Draft capacity plan doc with tradeoffs",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="queued",
            cost_usd=0.0,
            duration_min=0,
        ),
        Task(
            id="m6-t4",
            title="Walk plan through with leadership",
            assignee="@sam",
            assignee_kind="human",
            status="queued",
            cost_usd=0.0,
            duration_min=0,
        ),
    ],
    activity=[
        ActivityEvent("2h ago", "Sam created mission — tasks queued, not yet dispatched", kind="human"),
    ],
    last_activity="2h ago",
)

MISSIONS: List[Mission] = [MISSION_1, MISSION_2, MISSION_3, MISSION_4, MISSION_5, MISSION_6]


# ---------------------------------------------------------------------------
# Live regression-suite stream (Mission #1, Scout's running task)
# ---------------------------------------------------------------------------

# Cycles forever for demo purposes. The "running" snapshot freezes at 41/120,
# but the stream cycles so a viewer always sees motion.
REGRESSION_LINES: List[str] = [
    "running test 38/120: cart_apply_discount_idempotent ............ ok",
    "running test 39/120: checkout_3ds_challenge_redirect ........... ok",
    "running test 40/120: payment_retry_v2_e2e (eu-west-1) .......... ok",
    "running test 41/120: payment_retry_v2_e2e (eu-central-1) ....... ok",
    "running test 42/120: payment_retry_v2_e2e (us-east-1) .......... ok",
    "running test 43/120: payment_retry_v2_under_pool_pressure ...... FAIL",
    "  expected 200, got 503 — pool=20, waiting=22 (flaky?)",
    "running test 44/120: checkout_resume_after_3ds ................. ok",
    "running test 45/120: checkout_idempotency_key_replay ........... ok",
    "running test 46/120: payment_retry_v2_backoff_jitter ........... ok",
]


# ---------------------------------------------------------------------------
# Knowledge-base docs (auto-written from mission session logs)
# ---------------------------------------------------------------------------

POSTMORTEM_LATENCY = """\
# Postmortem: Weekend latency spike on /api/feed (2026-04-26)

> Auto-written from Mission #2 — *Investigate weekend latency spike*.
> 14 session events distilled. Owner: @sam.

## Summary

Between 02:07 UTC and 03:42 UTC on Saturday 2026-04-26, p99 latency on
`/api/feed` rose from a baseline of 180 ms to a peak of 4.2 s. The spike was
caused by a cache stampede triggered by the scheduled `feed-cache` restart at
02:00 UTC: every feed worker simultaneously rebuilt the same hot key, blocking
on the underlying Postgres read.

User impact: ~340 K feed requests served slower than 1 s; no errors, no data
loss. Mobile clients with a 5 s timeout retried, amplifying the load by ~1.4×
during the worst minute.

## Timeline (UTC)

- **02:00** — `feed-cache` deployment restart begins (weekly cron, intentional).
- **02:01** — All 6 feed-worker pods cold-start their L1 cache.
- **02:07** — p99 latency on `/api/feed` crosses 1 s. PagerDuty page deferred
  (warning threshold, not page).
- **02:43** — p99 peaks at 4.2 s. Mobile retry-amplification visible in
  `feed-service.requests_per_minute`.
- **03:14** — Cache fully repopulated; latency begins to recover.
- **03:42** — p99 back under 250 ms. Auto-resolve.
- **Sun 09:12** — Sam opens Mission #2, dispatches Scout to investigate.

## Root cause

The `feed-cache` restart cron is correct *in isolation* — the failure is in
the **cold-start path**. We have no `singleflight` / lock-on-miss for the top
50 hot keys. When all 6 workers see a cold cache simultaneously, they each
issue the same expensive Postgres query, queueing on the same row lock.

## Contributing factors

1. The hot-key set fits in a few KB but takes ~1.8 s to compute from Postgres.
2. Mobile-client retry policy is `min(5s, exp_backoff)` — too aggressive for
   a request that can legitimately take 2–4 s during a cold start.
3. The weekly cache-restart cron has no jitter; all pods restart in the same
   60-second window.

## What went well

- Auto-resolve fired correctly; no manual intervention needed at 02:00.
- The on-call runbook for `/api/feed` was accurate enough that Scout's
  investigation finished in 7 minutes.
- No data was served stale — the cache stampede was a latency event, not a
  correctness event.

## What didn't go well

- The warning-level threshold on p99 is too lax for a user-facing endpoint.
- We had no Honeycomb board pinned for `feed-cache` cold-start behavior.
- Mobile retry amplification was a known issue from a 2025-Q4 retro that
  never made it to a tracked action item.

## Action items

- [ ] **Add `singleflight` for the top 50 hot keys in `feed-cache`** — owner:
      @sam, target: end of week.
- [ ] **Add jitter (±10 min) to the weekly cache-restart cron** — owner: @maya,
      target: Friday.
- [ ] **Tighten p99 alert: page at 800 ms, not 1.5 s** — owner: @jake, target:
      end of week.
- [ ] **Mobile: switch to a longer first-try timeout (8 s) before retry** —
      owner: mobile team, target: next sprint.
- [ ] **Pin a Honeycomb board for `feed-cache` cold-start metrics** —
      owner: @sam, target: today.

## Source events

This document was auto-generated from 14 session events across the four tasks
in Mission #2 (root-cause investigation, weekend traffic baseline, remediation
plan, mitigation approval). [Open mission →](#mission/2)
"""

DECISION_PAYMENTS = """\
# Decision Log: EU payment provider

> Auto-written from Mission #3 — *Decide on payment provider for EU rollout*.
> 9 session events distilled. Owner: @jake. **Status: open — awaiting decision.**

## Decision context

We are launching in the EU in Q3. Our current payment stack (Stripe US-only)
does not support SEPA Direct Debit, iDEAL, or Bancontact natively, all of
which together account for ~62% of expected EU checkout volume based on the
Researcher's competitive scan.

Target: blended cost <2.4% of GMV, with a migration path that does not
require rewriting the checkout flow end-to-end.

## Options considered

| Provider | SEPA | iDEAL | Bancontact | Blended cost | Migration effort | Notes |
|---|---|---|---|---|---|---|
| Stripe (Treasury) | yes | yes | yes | 2.6% | low (existing integration) | Newest local-method support; some methods still beta in some EU markets. |
| Adyen | yes | yes | yes | 2.1% | medium | Strongest EU coverage; requires PSD2 onboarding flow rewrite. |
| Mollie | yes | yes | yes | 2.3% | medium | Good NL/BE; weaker DACH presence. |
| Checkout.com | yes | yes | yes | 2.2% | high | Best raw rates but no existing SDK in our stack. |

(Sources: each provider's published EU rate cards plus our 2025 Q4 GMV mix
distribution. Cost model spreadsheet linked from Mission #3.)

## Recommendation

**Adyen.** It wins on blended cost (2.1% vs 2.6% for staying with Stripe),
has the strongest local-method coverage, and the migration effort is bounded
to one team for one quarter.

The 0.5% blended-cost gap on projected 2027 EU GMV is ~$1.98M/year — large
enough to justify the migration cost and the PSD2 onboarding-flow rewrite.

## Open questions

1. Do we run Adyen and Stripe in parallel during a 3-month canary, or
   cut over fully on go-live? (Affects reconciliation work.)
2. PSD2 SCA fallback: can we share the same fallback flow across both
   providers? (Eng spike needed.)
3. Finance: who owns the multi-currency settlement reconciliation? Today
   it's a Google Sheet; that does not scale to 14 EU markets.

## Decision owner

@jake — pick provider by **next Wednesday**.
@maya — schedule the finance call this week to pressure-test the cost model
and reconciliation plan.
"""

DIGEST_WEEKLY = """\
# Weekly Engineering Digest — week of 2026-04-22

> Auto-aggregated from all open and recently-closed missions.
> 47 session events distilled across 4 missions. Generated Sunday evening.

## Shipped

- **Investigation of weekend `/api/feed` latency spike** (Mission #2) — root
  cause was a cache stampede on the weekly cache-restart cron; mitigation
  approved (TTL pin + jitter) and rolling out Mon-Tue. Postmortem filed.

## In-flight

- **Ship v2.4 of the checkout flow** (Mission #1) — retry-with-backoff PR
  merged; regression suite running on staging now (41/120). One blocker
  remaining: production rollout approval (waiting on regression-suite +
  canary metrics).
- **EU payment provider decision** (Mission #3) — decision memo posted;
  recommendation is Adyen at $1.98M/yr cost advantage. Two human tasks
  outstanding (finance call, final pick).

## Blocked

- Mission #1, *Approve production rollout* — @jake — needs the regression
  suite to finish first. Soft target Friday.

## New decisions

- Mission #3 produced a Decision Log recommending **Adyen** over Stripe for
  EU. Open until @jake signs off.

## Top agent runs this week

| Agent | Mission | Output |
|---|---|---|
| Researcher | #1 | Checkout error-rate audit across 4 regions |
| Researcher | #3 | EU payment-provider survey (4 providers) + cost model |
| Spec Drafter | #1 | retry-with-backoff spec |
| Spec Drafter | #3 | EU payment Decision Log |
| Scout | #2 | Root-cause investigation of weekend latency spike |
| Scout | #1 | Regression suite (in progress, 41/120) |
| PR Bot | #1 | Opened PR #1234 |

## Watch for next week

- Regression suite completes — if green, Mission #1 unblocks Friday.
- Finance call on EU payment provider — if it confirms the cost model,
  Mission #3 closes mid-week.
- Mission #4 (platform-eng onboarding) tasks dispatch Monday morning.
"""

SPEC_RETRY = """\
# Spec: retry-with-backoff for `/api/checkout`

> Auto-written from Mission #1 — *Ship v2.4 of the checkout flow*.
> Drafted by the Spec Drafter agent, reviewed by @maya. 6 session events distilled.

## Problem

The current `/api/checkout` flow surfaces transient `payments-service` failures
directly to the user as a 503. Based on the Researcher's audit (Mission #1,
Task 1), 14% of checkout 5xx errors over the last 30 days were transient
network blips that would have succeeded on a single retry.

## Requirements

1. Retry transient failures (HTTP 502, 503, 504, plus connection-reset) up to
   3 times before surfacing a 5xx to the user.
2. Use exponential backoff with jitter: 100 ms → 300 ms → 900 ms (±30%).
3. Do **not** retry on 4xx, on a `Idempotency-Key` conflict, or once the
   total request budget exceeds 4 s.
4. Bound the worst-case DB-connection hold time per checkout to 5 s — the
   pool size is 20, and we cannot regress under normal traffic.

## Design

A small `retry_transient(...)` helper in `checkout/payments_client.py`
wraps the HTTP call to `payments-service`. Configuration:

```python
RETRY = RetryPolicy(
    max_attempts=3,
    backoff_ms=[100, 300, 900],
    jitter=0.3,
    retryable_statuses={502, 503, 504},
    total_budget_ms=4000,
)
```

The helper releases the DB connection between retries (via `with_release()`),
so the pool is not held during backoff. This is the bug that caused the
2026-02 incident, and we are explicitly avoiding it here.

## Edge cases

- **Idempotency**: every retry uses the same `Idempotency-Key`; the upstream
  is responsible for deduplicating. We add an integration test for this.
- **3DS challenges**: a 3DS challenge response is not transient and must not
  be retried.
- **Long-running 503s**: if `payments-service` is genuinely down, we want to
  fail fast, not stack 3 retries × 6 EU regions × 4 s = 72 s of latency.
  The `total_budget_ms=4000` enforces this.

## Rollout plan

1. Land behind feature flag `payment_retry_v2`. (Done in PR #1234.)
2. Enable for 10% of EU traffic (canary). Hold for 24 h, watch:
   - p99 latency on `/api/checkout` (must stay <800 ms).
   - DB pool wait time (must stay <50 ms p99).
   - Error rate on `/api/checkout` (target: 30%+ reduction in 5xx).
3. If green, ramp to 50%, hold 24 h, then 100%.
4. Remove the flag after 2 weeks of clean canary.

## Open questions

- Should we surface a "retrying…" UX hint to the user when an attempt
  fails? Probably not for the first attempt; maybe for the second. Needs
  a design decision before 100% rollout.
"""

KB_DOCS: List[KbDoc] = [
    KbDoc(
        id=1,
        title="Postmortem: Weekend latency spike (2026-04-26)",
        category="postmortem",
        source_mission_id=2,
        source_mission_title="Investigate weekend latency spike",
        generated_at="2h ago",
        event_count=14,
        body=POSTMORTEM_LATENCY,
    ),
    KbDoc(
        id=2,
        title="Decision Log: EU payment provider",
        category="decision",
        source_mission_id=3,
        source_mission_title="Decide on payment provider for EU rollout",
        generated_at="6h ago",
        event_count=9,
        body=DECISION_PAYMENTS,
    ),
    KbDoc(
        id=3,
        title="Weekly Engineering Digest (week of 2026-04-22)",
        category="digest",
        source_mission_id=0,
        source_mission_title="All missions, week of 2026-04-22",
        generated_at="yesterday",
        event_count=47,
        body=DIGEST_WEEKLY,
    ),
    KbDoc(
        id=4,
        title="Spec: retry-with-backoff for /api/checkout",
        category="spec",
        source_mission_id=1,
        source_mission_title="Ship v2.4 of the checkout flow",
        generated_at="4h ago",
        event_count=6,
        body=SPEC_RETRY,
    ),
]


# ---------------------------------------------------------------------------
# Mission templates (used by the "Plan a mission" wizard + Templates tab)
# ---------------------------------------------------------------------------

# Each template entry:
#   key           : keyword
#   name          : display name
#   description   : short one-liner
#   icon          : svg symbol id ("i-tpl-incident", etc.)
#   used_count    : "Used N times" stat
#   tasks         : list of {title, assignee, assignee_kind, est_min, why}
#
# Keywords (lower-cased, substring match) route a free-text outcome to a
# template. Falls back to "generic" when no keyword hits.

MISSION_TEMPLATES: dict = {
    "incident": {
        "key": "incident",
        "name": "Investigate Incident",
        "description": "Production incident, latency spike, error-rate regression — figure out what broke and ship a fix.",
        "icon": "i-tpl-incident",
        "used_count": 14,
        "keywords": ["incident", "latency", "spike", "outage", "bug", "regression", "error", "alert", "p99", "5xx"],
        "tasks": [
            {"title": "Investigate root cause", "assignee": "Scout", "assignee_kind": "agent",
             "est_min": 25, "why": "Scout is fastest at log/trace triage and proposes a root-cause hypothesis."},
            {"title": "Compare with last 4 weekends' baseline", "assignee": "Researcher", "assignee_kind": "agent",
             "est_min": 18, "why": "Confirms whether this is novel or a known seasonal pattern."},
            {"title": "Draft remediation plan", "assignee": "Spec Drafter", "assignee_kind": "agent",
             "est_min": 25, "why": "Produces a reviewable plan once the cause is known."},
            {"title": "Approve mitigation", "assignee": "@sam", "assignee_kind": "human",
             "est_min": 15, "why": "Human sign-off before any prod-touching change."},
            {"title": "Auto-write postmortem", "assignee": "Spec Drafter", "assignee_kind": "agent",
             "est_min": 20, "why": "Closes the loop into the Knowledge Base."},
        ],
    },
    "research": {
        "key": "research",
        "name": "Research Competitor",
        "description": "Survey the market, build a comparison, and produce a decision memo.",
        "icon": "i-tpl-telescope",
        "used_count": 9,
        "keywords": ["competitor", "pricing", "research", "survey", "benchmark", "market", "scan", "analysis"],
        "tasks": [
            {"title": "Survey 4 candidate vendors / competitors", "assignee": "Researcher", "assignee_kind": "agent",
             "est_min": 50, "why": "Pulls public pricing, docs, and rate cards into one brief."},
            {"title": "Build cost / feature comparison spreadsheet", "assignee": "Researcher", "assignee_kind": "agent",
             "est_min": 35, "why": "Makes the tradeoff math concrete and reviewable."},
            {"title": "Compile decision memo with tradeoffs", "assignee": "Spec Drafter", "assignee_kind": "agent",
             "est_min": 30, "why": "Turns the comparison into a recommendation."},
            {"title": "Pick winner", "assignee": "@jake", "assignee_kind": "human",
             "est_min": 25, "why": "Decision is reversible-ish but high-stakes — human owner."},
        ],
    },
    "ship": {
        "key": "ship",
        "name": "Ship Feature",
        "description": "Take a feature from design through PR, regression, and rollout.",
        "icon": "i-tpl-rocket",
        "used_count": 23,
        "keywords": ["ship", "feature", "release", "rollout", "launch", "build", "implement", "deploy"],
        "tasks": [
            {"title": "Audit current behavior + data", "assignee": "Researcher", "assignee_kind": "agent",
             "est_min": 20, "why": "Grounds the spec in real production numbers."},
            {"title": "Draft spec for the new feature", "assignee": "Spec Drafter", "assignee_kind": "agent",
             "est_min": 25, "why": "Produces a reviewable spec before any code is written."},
            {"title": "Open PR with implementation", "assignee": "PR Bot", "assignee_kind": "agent",
             "est_min": 45, "why": "Implements the spec; human reviews."},
            {"title": "Review PR", "assignee": "@maya", "assignee_kind": "human",
             "est_min": 20, "why": "Human review on every code change."},
            {"title": "Run regression suite against staging", "assignee": "Scout", "assignee_kind": "agent",
             "est_min": 30, "why": "Catches integration regressions before prod."},
            {"title": "Approve production rollout", "assignee": "@jake", "assignee_kind": "human",
             "est_min": 10, "why": "Last gate before traffic flips."},
        ],
    },
    "onboard": {
        "key": "onboard",
        "name": "Onboard New Hire",
        "description": "Get a new teammate productive: access, docs, shadow rotation, real first task.",
        "icon": "i-tpl-handshake",
        "used_count": 6,
        "keywords": ["onboard", "new-hire", "new hire", "team", "hires", "ramp", "intern"],
        "tasks": [
            {"title": "Pull recent platform issues from incident DB", "assignee": "Researcher", "assignee_kind": "agent",
             "est_min": 20, "why": "Gives the new hire a feel for what real work looks like."},
            {"title": "Draft onboarding doc skeleton", "assignee": "Spec Drafter", "assignee_kind": "agent",
             "est_min": 30, "why": "Standard sections — fill in the blanks."},
            {"title": "Provision prod access + on-call shadow", "assignee": "@maya", "assignee_kind": "human",
             "est_min": 25, "why": "Human-only; cannot be delegated to an agent."},
            {"title": "Pair on first real PR", "assignee": "@jake", "assignee_kind": "human",
             "est_min": 60, "why": "First-PR pairing builds context faster than docs."},
            {"title": "Compile day-7 retro", "assignee": "Spec Drafter", "assignee_kind": "agent",
             "est_min": 15, "why": "Closes the loop on what worked / didn't for next hire."},
        ],
    },
    "migrate": {
        "key": "migrate",
        "name": "Migrate Database",
        "description": "Schema migration with dual-write, backfill, and cutover.",
        "icon": "i-tpl-database",
        "used_count": 4,
        "keywords": ["migrate", "migration", "schema", "backfill", "dual-write"],
        "tasks": [
            {"title": "Audit current schema usage", "assignee": "Researcher", "assignee_kind": "agent",
             "est_min": 25, "why": "Find every callsite that reads or writes the table."},
            {"title": "Draft dual-write migration plan", "assignee": "Spec Drafter", "assignee_kind": "agent",
             "est_min": 30, "why": "Plan must show forward, dual-write, and cutover phases."},
            {"title": "Generate forward + backfill migrations", "assignee": "Migrator", "assignee_kind": "agent",
             "est_min": 45, "why": "Produces the actual SQL plus a backfill job."},
            {"title": "Dry-run against staging snapshot", "assignee": "Scout", "assignee_kind": "agent",
             "est_min": 20, "why": "Catches NOT-NULL surprises before prod."},
            {"title": "Approve forward migration", "assignee": "@sam", "assignee_kind": "human",
             "est_min": 15, "why": "Human gate before destructive prod change."},
            {"title": "Run forward migration in prod", "assignee": "Migrator", "assignee_kind": "agent",
             "est_min": 25, "why": "Executes with monitoring + rollback ready."},
            {"title": "Cut readers over and drop dual-write", "assignee": "PR Bot", "assignee_kind": "agent",
             "est_min": 30, "why": "Closes the migration."},
        ],
    },
    "plan": {
        "key": "plan",
        "name": "Quarterly Plan",
        "description": "Capacity planning, budget asks, and explicit non-bets for the next quarter.",
        "icon": "i-tpl-calendar",
        "used_count": 3,
        "keywords": ["plan", "quarter", "quarterly", "capacity", "budget", "okr", "headcount"],
        "tasks": [
            {"title": "Pull last 6 weeks of utilization data", "assignee": "Researcher", "assignee_kind": "agent",
             "est_min": 20, "why": "Grounds plan in actual utilization, not vibes."},
            {"title": "Survey each team lead on top 2 bets", "assignee": "Researcher", "assignee_kind": "agent",
             "est_min": 30, "why": "Turns offline conversations into a single document."},
            {"title": "Draft capacity plan with tradeoffs", "assignee": "Spec Drafter", "assignee_kind": "agent",
             "est_min": 45, "why": "Hard part: spell out what we are *not* doing."},
            {"title": "Walk plan through with leadership", "assignee": "@sam", "assignee_kind": "human",
             "est_min": 60, "why": "Final negotiations require a human in the room."},
        ],
    },
    "generic": {
        "key": "generic",
        "name": "Generic Mission",
        "description": "We didn't recognize the outcome — here's a balanced agent + human plan.",
        "icon": "i-tpl-rocket",
        "used_count": 0,
        "keywords": [],
        "tasks": [
            {"title": "Research and gather context", "assignee": "Researcher", "assignee_kind": "agent",
             "est_min": 25, "why": "Always start with grounded context."},
            {"title": "Draft proposal", "assignee": "Spec Drafter", "assignee_kind": "agent",
             "est_min": 25, "why": "Produces a reviewable artifact."},
            {"title": "Open PR / implementation", "assignee": "PR Bot", "assignee_kind": "agent",
             "est_min": 35, "why": "Carries the proposal into code if applicable."},
            {"title": "Review", "assignee": "@maya", "assignee_kind": "human",
             "est_min": 15, "why": "Human review on the artifact."},
            {"title": "Approve / land", "assignee": "@jake", "assignee_kind": "human",
             "est_min": 10, "why": "Final gate."},
        ],
    },
}


def match_template(outcome: str) -> str:
    """Return the template key whose keyword set best matches `outcome`."""
    text = (outcome or "").lower()
    for key, tpl in MISSION_TEMPLATES.items():
        if key == "generic":
            continue
        for kw in tpl["keywords"]:
            if kw in text:
                return key
    return "generic"


# ---------------------------------------------------------------------------
# Per-task session logs (replayed in the "View session log" modal)
# ---------------------------------------------------------------------------

def _log(*lines: str) -> List[str]:
    return list(lines)


TASK_SESSION_LOGS: dict = {
    "m1-t1": _log(
        "[setup] provisioning Sprite (vm-72ad)",
        "[setup] cloning checkout-service@main",
        "[setup] python -m venv .venv && pip install -e .",
        "[setup] env hydrated: HONEYCOMB_API_KEY (encrypted), READ_REPLICA_DSN",
        "[agent] Researcher v2.3 booting",
        "[agent] goal: 'Audit current checkout error rates by region'",
        "[think] Plan: pull last 30 days of /api/checkout 5xx, segmented by region.",
        "[tool] honeycomb.run_query(dataset='checkout-prod', breakdowns=['region'], filter='status >= 500')",
        "[tool]   -> 4 regions, 12 columns, 30 days",
        "[think] EU regions show 3.1× US error rate; need to confirm with raw events.",
        "[tool] honeycomb.run_query(dataset='checkout-prod', filter='region in (eu-west-1, eu-central-1) and status=503')",
        "[tool]   -> 41,222 events; top error: 'payments-service: connection reset'",
        "[think] Connection-reset errors look transient. Hypothesis: retry-with-backoff would absorb.",
        "[tool] grep -rn 'payments_client' src/checkout",
        "[tool]   -> 3 callsites, none retry on transient errors",
        "[tool] git log --since='30 days ago' src/checkout/payments_client.py",
        "[tool]   -> last touched 8 months ago, no retry logic added",
        "[think] Confidence high. Drafting brief.",
        "[output] # Brief: Checkout error-rate audit",
        "[output] - 14% of checkout 5xx are transient (connection-reset, 503)",
        "[output] - EU regions are 3.1× US baseline",
        "[output] - retry-with-backoff is the obvious mitigation",
        "[output] - cost model: ~$8K/month savings on lost-cart recovery",
        "[result] artifact uploaded: brief.md (1.4 KB)",
        "[result] mission #1 task 1 complete (18 min, $1.42)",
    ),
    "m1-t2": _log(
        "[setup] provisioning Sprite (vm-7ab3)",
        "[setup] cloning checkout-service@main",
        "[agent] Spec Drafter v1.7 booting",
        "[agent] goal: 'Draft spec for retry-with-backoff'",
        "[think] Reading the brief from task 1. Three constraints worth flagging:",
        "[think]   1. DB pool is 20 connections — cannot hold during backoff.",
        "[think]   2. 3DS challenges are not transient — never retry.",
        "[think]   3. Idempotency-Key replay: upstream must dedupe.",
        "[tool] open src/checkout/payments_client.py",
        "[tool] open src/checkout/db_pool.py",
        "[tool] open docs/runbooks/payments_outage.md",
        "[think] Writing spec sections: problem, requirements, design, edge cases, rollout.",
        "[tool] write spec.md ...",
        "[think] Adding a 4 s total budget so a flapping payments-service doesn't stack 72 s of latency.",
        "[think] Including the singleflight insight from incident #2024-11-03.",
        "[tool] mention links: incident docs, prior PR #998 (the bug we are NOT repeating).",
        "[output] # Spec: retry-with-backoff for /api/checkout",
        "[output] (full spec written — 4 sections, 1 code block, 1 rollout plan)",
        "[result] artifact uploaded: spec.md (3.8 KB)",
        "[result] mission #1 task 2 complete (24 min, $2.18)",
    ),
    "m1-t3": _log(
        "[setup] provisioning Sprite (vm-99c1)",
        "[setup] cloning checkout-service@main",
        "[setup] git checkout -b retry-with-backoff",
        "[agent] PR Bot v3.0 booting",
        "[agent] goal: 'Open PR with retry implementation per spec.md'",
        "[think] Plan: add retry_transient helper, wire into payments_client, add unit + integration tests.",
        "[tool] read spec.md (from mission #1 task 2)",
        "[tool] read src/checkout/payments_client.py",
        "[tool] write src/checkout/retry.py",
        "[tool]   - RetryPolicy dataclass",
        "[tool]   - retry_transient(req, policy) helper",
        "[tool] edit src/checkout/payments_client.py",
        "[tool]   - wrap charge() and refund() with retry_transient",
        "[tool] write tests/test_retry.py",
        "[tool]   - test_retry_on_503",
        "[tool]   - test_no_retry_on_400",
        "[tool]   - test_total_budget_enforced",
        "[tool]   - test_idempotency_key_preserved",
        "[tool]   - test_3ds_challenge_not_retried",
        "[tool] pytest tests/test_retry.py -q",
        "[tool]   ......... 9 passed in 0.41s",
        "[tool] ruff check src/checkout/",
        "[tool]   All checks passed!",
        "[tool] git add . && git commit -m 'Add retry-with-backoff for transient payment failures'",
        "[tool] git push -u origin retry-with-backoff",
        "[tool] gh pr create --title 'retry-with-backoff for /api/checkout' --body '@spec spec.md'",
        "[tool]   -> PR #1234 opened",
        "[output] PR #1234: retry-with-backoff for /api/checkout",
        "[output] +218 lines / -4 lines, 9 tests passing, ruff clean",
        "[result] artifact uploaded: PR #1234",
        "[result] mission #1 task 3 complete (41 min, $3.74)",
    ),
    "m1-t4": _log(
        "[review] @maya opened PR #1234",
        "[review] read 218 lines of diff",
        "[review] read tests/test_retry.py",
        "[review] inline comment on retry.py:42 — 'jitter on the floor too, please'",
        "[review] PR Bot pushed fixup commit addressing comment",
        "[review] @maya re-read affected hunks",
        "[review] approved with 'lgtm — nice tests'",
        "[result] mission #1 task 4 complete (22 min)",
    ),
    "m2-t1": _log(
        "[setup] provisioning Sprite (vm-44ef)",
        "[setup] cloning feed-service@main",
        "[setup] env hydrated: HONEYCOMB_API_KEY, PG_REPLICA_DSN",
        "[agent] Scout v4.2 booting",
        "[agent] goal: 'Investigate root cause of weekend p99 spike on /api/feed'",
        "[think] Window: Sat 02:07 UTC – 03:42 UTC. Spike from 180ms baseline to 4.2s.",
        "[tool] honeycomb.run_query(dataset='feed-prod', filter='endpoint=/api/feed and duration_ms > 1000', breakdowns=['pod'])",
        "[tool]   -> 6 pods all spiking simultaneously",
        "[think] All pods spiking together rules out a single bad pod. Suggests cache or upstream.",
        "[tool] honeycomb.run_query(dataset='feed-prod', breakdowns=['pg_query'], time_range='2026-04-26 02:00 - 03:00')",
        "[tool]   -> top query: 'SELECT * FROM feed_index WHERE shard=$1' x 6,840 calls",
        "[think] 6,840 calls of an expensive query in an hour — that's stampede shaped.",
        "[tool] grep -rn 'feed-cache' deploy/",
        "[tool]   -> deploy/cron.yaml: feed-cache restart at 02:00 UTC weekly",
        "[think] Cron restarts feed-cache, all pods cold-start L1, all pods race to rebuild same hot keys.",
        "[tool] open src/feed/cache.py",
        "[tool]   -> no singleflight, no lock-on-miss",
        "[output] # Investigation: weekend p99 latency spike",
        "[output] Root cause: cache stampede on weekly feed-cache restart cron",
        "[output] Trigger: 02:00 UTC restart -> all 6 pods cold-start L1 -> race on hot keys",
        "[output] No singleflight in src/feed/cache.py",
        "[result] artifact uploaded: investigation.md",
        "[result] mission #2 task 1 complete (7 min, $2.92)",
    ),
    "m2-t2": _log(
        "[setup] provisioning Sprite (vm-2bd4)",
        "[agent] Researcher v2.3 booting",
        "[agent] goal: 'Compare with last 4 weekends' traffic'",
        "[tool] honeycomb.run_query(dataset='feed-prod', breakdowns=['day'], time_range='last 4 weeks')",
        "[think] Pattern check across 4 weekends.",
        "[tool] honeycomb.run_query(dataset='feed-prod', filter='hour=2 and day_of_week=Sat', time_range='last 4 weeks')",
        "[think] Last 3 Saturdays at 02:00 UTC: p99 jumps but recovers in <2 min.",
        "[think] This Saturday: 90 minutes to recover. Outlier.",
        "[tool] honeycomb.run_query(dataset='feed-prod', filter='deploy.event=true', time_range='last 4 weeks')",
        "[think] Deploy log: feed-cache restart added cold-start hot-key prefetch removal 2 weeks ago.",
        "[output] # Brief: weekend traffic comparison",
        "[output] - last 3 Saturdays recovered in <2min",
        "[output] - this Saturday 90min — outlier",
        "[output] - deploy 2 weeks ago removed hot-key prefetch from cache cold-start",
        "[output] - the prefetch was effectively the singleflight",
        "[result] artifact uploaded: brief.md",
        "[result] mission #2 task 2 complete (14 min, $1.85)",
    ),
    "m2-t3": _log(
        "[setup] provisioning Sprite (vm-3a01)",
        "[agent] Spec Drafter v1.7 booting",
        "[agent] goal: 'Draft remediation plan for feed-cache stampede'",
        "[think] Three options: (1) add singleflight, (2) re-add prefetch, (3) jitter restart cron.",
        "[think] Recommendation: do all three. (1) and (2) are independent fixes; (3) is cheap.",
        "[tool] open src/feed/cache.py",
        "[tool] research: 'singleflight implementation in python'",
        "[tool] write plan.md with three sections",
        "[output] # Plan: feed-cache cold-start mitigation",
        "[output] 1. Add singleflight for top 50 hot keys (1d)",
        "[output] 2. Re-add hot-key prefetch on cold-start (4h)",
        "[output] 3. Add ±10min jitter to weekly restart cron (1h)",
        "[output] Pin TTL to 90s during ramp.",
        "[result] artifact uploaded: plan.md",
        "[result] mission #2 task 3 complete (21 min, $2.36)",
    ),
    "m2-t4": _log(
        "[review] @sam opened plan.md",
        "[review] questions on cost of singleflight allocation",
        "[review] Spec Drafter responded: 'sync.Once style, no extra goroutines'",
        "[review] @sam approved 'pin feed-cache TTL to 90s'",
        "[result] mission #2 task 4 complete (18 min)",
    ),
    "m3-t1": _log(
        "[setup] provisioning Sprite (vm-512f)",
        "[agent] Researcher v2.3 booting",
        "[agent] goal: 'Survey 4 candidate payment providers for EU rollout'",
        "[think] Candidates: Stripe Treasury, Adyen, Mollie, Checkout.com",
        "[tool] webfetch https://stripe.com/pricing/local-payment-methods",
        "[tool] webfetch https://www.adyen.com/pricing",
        "[tool] webfetch https://www.mollie.com/en/pricing",
        "[tool] webfetch https://www.checkout.com/pricing",
        "[think] Need to confirm SEPA/iDEAL/Bancontact across all four.",
        "[tool] webfetch each provider's docs, extract supported methods table",
        "[think] All four support all three. Differentiation is on rates and migration effort.",
        "[tool] webfetch existing integration code in our checkout repo",
        "[tool]   -> Stripe SDK already in stack. Adyen would need new SDK.",
        "[output] # Brief: EU payment-provider survey",
        "[output] | Provider | SEPA | iDEAL | Bancontact | Migration |",
        "[output] | Stripe   | y    | y     | y          | low       |",
        "[output] | Adyen    | y    | y     | y          | medium    |",
        "[output] | Mollie   | y    | y     | y          | medium    |",
        "[output] | Checkout | y    | y     | y          | high      |",
        "[result] artifact uploaded: brief.md",
        "[result] mission #3 task 1 complete (52 min, $4.18)",
    ),
    "m3-t2": _log(
        "[setup] provisioning Sprite (vm-67aa)",
        "[agent] Researcher v2.3 booting",
        "[agent] goal: 'Build EU payment-provider cost model'",
        "[tool] read brief from task 1",
        "[tool] read internal: 2025 Q4 GMV mix by EU country",
        "[tool] webfetch each provider's published rate card",
        "[think] Modeling at projected 2027 EU GMV ($395M).",
        "[tool] write costmodel.csv",
        "[tool]   -> Stripe blended: 2.6% = $10.27M",
        "[tool]   -> Adyen blended:  2.1% =  $8.30M",
        "[tool]   -> Mollie blended: 2.3% =  $9.08M",
        "[tool]   -> Checkout:       2.2% =  $8.69M",
        "[think] Adyen wins by $1.98M/yr vs Stripe.",
        "[output] # Cost model: EU payment providers",
        "[output] Adyen wins on blended rate, $1.98M/yr advantage over Stripe at projected 2027 GMV",
        "[result] artifact uploaded: costmodel.csv + summary.md",
        "[result] mission #3 task 2 complete (38 min, $3.27)",
    ),
    "m3-t3": _log(
        "[setup] provisioning Sprite (vm-71b8)",
        "[agent] Spec Drafter v1.7 booting",
        "[agent] goal: 'Compile decision memo with tradeoffs'",
        "[tool] read brief.md and costmodel.csv from prior tasks",
        "[think] Memo structure: context, options, recommendation, open questions, decision owner.",
        "[tool] write decision.md",
        "[output] # Decision Log: EU payment provider",
        "[output] Recommendation: Adyen ($1.98M/yr advantage)",
        "[output] Open questions: parallel canary vs cutover, PSD2 SCA fallback, finance reconciliation",
        "[output] Decision owner: @jake (next Wednesday)",
        "[result] artifact uploaded: decision.md",
        "[result] mission #3 task 3 complete (29 min, $2.95)",
    ),
    "m5-t1": _log(
        "[setup] provisioning Sprite (vm-91dc)",
        "[agent] Researcher v2.3 booting",
        "[agent] goal: 'Audit current notifications schema usage'",
        "[tool] grep -rn 'notifications' src/ --include='*.py'",
        "[tool]   -> 8 callsites across 4 services",
        "[tool] read each callsite",
        "[think] 6 are reads, 2 are writes. One read uses an undocumented column 'flags' as a JSON blob.",
        "[tool] honeycomb.run_query(dataset='notifications-prod', breakdowns=['op_name'], time_range='last 7d')",
        "[think] 'flags' column is read 14M times/day. Cannot drop blindly.",
        "[output] # Audit: notifications schema usage",
        "[output] - 8 callsites (6 reads, 2 writes)",
        "[output] - 'flags' column undocumented but heavily used",
        "[output] - need to type 'flags' in v3 not just rename",
        "[result] artifact uploaded: audit.md",
        "[result] mission #5 task 1 complete (22 min, $2.04)",
    ),
    "m5-t3": _log(
        "[setup] provisioning Sprite (vm-44a8)",
        "[agent] Migrator v0.9 booting",
        "[agent] goal: 'Generate forward + backfill migrations for notifications v3'",
        "[think] Plan: dual-write fence, backfill in batches of 5K, cutover behind feature flag.",
        "[tool] read migration plan from task 2",
        "[tool] write migrations/0312_notifications_v3_dual_write.py",
        "[tool] write migrations/0313_notifications_v3_backfill.py",
        "[tool] write src/notifications/dual_write.py",
        "[tool] pytest tests/test_notifications_dual_write.py",
        "[tool]   ........... 11 passed",
        "[think] One ambiguous case: notifications.priority is INT in old schema but spec calls for ENUM.",
        "[think] Cannot decide unilaterally — flagging for human.",
        "[tool] gh pr create --title 'notifications v3 dual-write + backfill' --body 'see plan; flagging priority enum'",
        "[output] PR #1290 opened",
        "[output] FLAG: priority column — INT vs ENUM is ambiguous, need human call",
        "[result] artifact uploaded: PR #1290",
        "[result] mission #5 task 3 complete (46 min, $4.38)",
    ),
    "m5-t4": _log(
        "[setup] provisioning Sprite (vm-58c2)",
        "[agent] Scout v4.2 booting",
        "[agent] goal: 'Dry-run backfill against staging snapshot'",
        "[tool] aod.snapshots.restore('prod-2026-04-28', target='staging')",
        "[tool] python manage.py migrate notifications",
        "[tool]   -> applied 0312_notifications_v3_dual_write OK",
        "[tool] python manage.py backfill_notifications --batch=5000",
        "[tool]   -> 14,221,008 rows in 6m 12s",
        "[tool]   -> 0 errors, 0 NULL violations",
        "[tool] sample 1000 random rows, compare v2 vs v3 representation",
        "[tool]   -> 1000/1000 match",
        "[output] # Dry-run report",
        "[output] - 14.2M rows backfilled in 6m 12s",
        "[output] - 0 errors, 0 NULL violations",
        "[output] - 1000-row sample: 100% match",
        "[result] artifact uploaded: dryrun.md",
        "[result] mission #5 task 4 complete (15 min, $1.79)",
    ),
}


# ---------------------------------------------------------------------------
# Activity firehose (right rail "All missions" toggle)
# ---------------------------------------------------------------------------

# 25 events covering the last 24h, newest first.
FIREHOSE_EVENTS: List[dict] = [
    {"timestamp": "just now", "mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "progressed", "text": "Scout: regression suite at 41/120"},
    {"timestamp": "4m ago", "mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "started", "text": "Scout started regression suite (120 tests)"},
    {"timestamp": "30m ago", "mission_id": 5, "mission_title": "Migrate user notifications to v3 schema",
     "kind": "blocked", "text": "Migrator: surfaced ambiguous column for human review"},
    {"timestamp": "1h ago", "mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "approved", "text": "@maya approved PR #1234"},
    {"timestamp": "1h ago", "mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "progressed", "text": "PR Bot opened PR #1234"},
    {"timestamp": "2h ago", "mission_id": 5, "mission_title": "Migrate user notifications to v3 schema",
     "kind": "completed", "text": "Scout: dry-run backfill clean against staging snapshot"},
    {"timestamp": "2h ago", "mission_id": 6, "mission_title": "Q2 capacity planning",
     "kind": "started", "text": "Sam created mission — tasks queued"},
    {"timestamp": "2h ago", "mission_id": 2, "mission_title": "Investigate weekend latency spike",
     "kind": "completed", "text": "Postmortem auto-written and filed"},
    {"timestamp": "3h ago", "mission_id": 2, "mission_title": "Investigate weekend latency spike",
     "kind": "approved", "text": "@sam approved mitigation plan"},
    {"timestamp": "4h ago", "mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "completed", "text": "Spec Drafter: posted retry-with-backoff spec"},
    {"timestamp": "5h ago", "mission_id": 2, "mission_title": "Investigate weekend latency spike",
     "kind": "completed", "text": "Spec Drafter: remediation plan ready for review"},
    {"timestamp": "6h ago", "mission_id": 3, "mission_title": "Decide on payment provider for EU rollout",
     "kind": "completed", "text": "Spec Drafter: decision memo posted to KB"},
    {"timestamp": "8h ago", "mission_id": 3, "mission_title": "Decide on payment provider for EU rollout",
     "kind": "commented", "text": "@maya: 'pulling finance into Thursday's call'"},
    {"timestamp": "12h ago", "mission_id": 5, "mission_title": "Migrate user notifications to v3 schema",
     "kind": "progressed", "text": "Migrator: opened PR #1290"},
    {"timestamp": "yesterday", "mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "completed", "text": "Researcher: checkout error-rate audit (4 regions)"},
    {"timestamp": "yesterday", "mission_id": 3, "mission_title": "Decide on payment provider for EU rollout",
     "kind": "completed", "text": "Researcher: cost model finalized (Adyen $1.98M/yr)"},
    {"timestamp": "yesterday", "mission_id": 2, "mission_title": "Investigate weekend latency spike",
     "kind": "completed", "text": "Researcher: weekend traffic baseline complete"},
    {"timestamp": "yesterday", "mission_id": 2, "mission_title": "Investigate weekend latency spike",
     "kind": "completed", "text": "Scout: root cause = cache stampede"},
    {"timestamp": "yesterday", "mission_id": 5, "mission_title": "Migrate user notifications to v3 schema",
     "kind": "completed", "text": "Spec Drafter: dual-write plan posted"},
    {"timestamp": "2d ago", "mission_id": 3, "mission_title": "Decide on payment provider for EU rollout",
     "kind": "completed", "text": "Researcher: provider survey complete (4 candidates)"},
    {"timestamp": "2d ago", "mission_id": 5, "mission_title": "Migrate user notifications to v3 schema",
     "kind": "completed", "text": "Researcher: schema audit complete (8 callsites)"},
    {"timestamp": "3d ago", "mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "started", "text": "Maya started mission"},
    {"timestamp": "3d ago", "mission_id": 2, "mission_title": "Investigate weekend latency spike",
     "kind": "started", "text": "Sam started mission from PagerDuty alert"},
    {"timestamp": "5d ago", "mission_id": 5, "mission_title": "Migrate user notifications to v3 schema",
     "kind": "started", "text": "Sam started mission"},
    {"timestamp": "6d ago", "mission_id": 3, "mission_title": "Decide on payment provider for EU rollout",
     "kind": "started", "text": "Jake started mission"},
]

# Ticker queue — these get appended one at a time every ~8s on the firehose.
FIREHOSE_TICKER: List[dict] = [
    {"mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "progressed", "text": "Scout: regression suite at 47/120"},
    {"mission_id": 5, "mission_title": "Migrate user notifications to v3 schema",
     "kind": "commented", "text": "@sam: 'priority should be ENUM, not INT'"},
    {"mission_id": 3, "mission_title": "Decide on payment provider for EU rollout",
     "kind": "commented", "text": "@maya: 'finance call locked for Thursday 2pm'"},
    {"mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "progressed", "text": "Scout: regression suite at 53/120"},
    {"mission_id": 6, "mission_title": "Q2 capacity planning",
     "kind": "progressed", "text": "Researcher dispatched on capacity-data pull"},
    {"mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "progressed", "text": "Scout: regression suite at 59/120"},
    {"mission_id": 5, "mission_title": "Migrate user notifications to v3 schema",
     "kind": "approved", "text": "@sam approved priority ENUM — Migrator unblocked"},
    {"mission_id": 1, "mission_title": "Ship v2.4 of the checkout flow",
     "kind": "progressed", "text": "Scout: regression suite at 65/120"},
]


# ---------------------------------------------------------------------------
# Workspace-level metrics (top bar)
# ---------------------------------------------------------------------------

WORKSPACE_METRICS: dict = {
    "in_flight": 5,
    "tasks_running": 32,
    "agents_active": 4,
    "spent_today_usd": 47.82,
    "awaiting_humans": 11,
}


# ---------------------------------------------------------------------------
# Agent / human profiles (for assignee popovers)
# ---------------------------------------------------------------------------

AGENT_PROFILES: dict = {
    "Researcher": {
        "kind": "agent",
        "name": "Researcher",
        "tagline": "Surveys, briefs, cost models",
        "model": "claude-opus-4-7",
        "recent_runs": 41,
        "success_rate": "97%",
        "forge_url": "https://aod.example.com/agents/researcher",
    },
    "Spec Drafter": {
        "kind": "agent",
        "name": "Spec Drafter",
        "tagline": "Turns context into reviewable specs",
        "model": "claude-opus-4-7",
        "recent_runs": 28,
        "success_rate": "100%",
        "forge_url": "https://aod.example.com/agents/spec-drafter",
    },
    "Scout": {
        "kind": "agent",
        "name": "Scout",
        "tagline": "Investigation, log triage, regression",
        "model": "claude-sonnet-4-7",
        "recent_runs": 62,
        "success_rate": "94%",
        "forge_url": "https://aod.example.com/agents/scout",
    },
    "PR Bot": {
        "kind": "agent",
        "name": "PR Bot",
        "tagline": "Implements specs, opens PRs",
        "model": "claude-opus-4-7",
        "recent_runs": 19,
        "success_rate": "89%",
        "forge_url": "https://aod.example.com/agents/pr-bot",
    },
    "Migrator": {
        "kind": "agent",
        "name": "Migrator",
        "tagline": "Schema + data migrations w/ dual-write",
        "model": "claude-opus-4-7",
        "recent_runs": 7,
        "success_rate": "100%",
        "forge_url": "https://aod.example.com/agents/migrator",
    },
    "@maya": {
        "kind": "human",
        "name": "Maya Chen",
        "tagline": "Eng lead — checkout & payments",
        "role": "Engineering Lead",
        "load": "3 missions, 2 reviews pending",
    },
    "@jake": {
        "kind": "human",
        "name": "Jake Patel",
        "tagline": "Founding eng — generalist",
        "role": "Founding Engineer",
        "load": "2 missions, 1 decision pending",
    },
    "@sam": {
        "kind": "human",
        "name": "Sam Ortega",
        "tagline": "SRE / platform",
        "role": "Platform & SRE",
        "load": "3 missions, 1 review pending",
    },
}
