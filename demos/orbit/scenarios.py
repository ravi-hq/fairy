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
        ),
        Task(
            id="m1-t2",
            title="Draft spec for retry-with-backoff",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="done",
            artifact_label="Spec doc",
            artifact_kind="doc",
        ),
        Task(
            id="m1-t3",
            title="Open PR with retry implementation",
            assignee="PR Bot",
            assignee_kind="agent",
            status="done",
            artifact_label="PR #1234",
            artifact_kind="pr",
        ),
        Task(
            id="m1-t4",
            title="Review PR #1234",
            assignee="@maya",
            assignee_kind="human",
            status="done",
        ),
        Task(
            id="m1-t5",
            title="Run regression suite against staging",
            assignee="Scout",
            assignee_kind="agent",
            status="running",
            detail="running test 41/120: payment_retry_v2_e2e",
        ),
        Task(
            id="m1-t6",
            title="Approve production rollout",
            assignee="@jake",
            assignee_kind="human",
            status="blocked",
            detail="Waiting on regression suite + canary metrics",
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
        ),
        Task(
            id="m2-t2",
            title="Compare with last 4 weekends' traffic",
            assignee="Researcher",
            assignee_kind="agent",
            status="done",
            artifact_label="Brief",
            artifact_kind="brief",
        ),
        Task(
            id="m2-t3",
            title="Draft remediation plan",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="done",
            artifact_label="Plan doc",
            artifact_kind="doc",
        ),
        Task(
            id="m2-t4",
            title="Approve mitigation",
            assignee="@sam",
            assignee_kind="human",
            status="done",
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
        ),
        Task(
            id="m3-t2",
            title="Build cost-model spreadsheet",
            assignee="Researcher",
            assignee_kind="agent",
            status="done",
            artifact_label="Cost model",
            artifact_kind="doc",
        ),
        Task(
            id="m3-t3",
            title="Compile decision memo with tradeoffs",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="done",
            artifact_label="Decision Log",
            artifact_kind="doc",
        ),
        Task(
            id="m3-t4",
            title="Schedule call with finance",
            assignee="@maya",
            assignee_kind="human",
            status="awaiting",
            detail="Two slots offered — awaiting finance reply",
        ),
        Task(
            id="m3-t5",
            title="Pick provider",
            assignee="@jake",
            assignee_kind="human",
            status="awaiting",
            detail="Decision memo open in Knowledge Base",
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
        ),
        Task(
            id="m4-t2",
            title="Draft onboarding doc skeleton",
            assignee="Spec Drafter",
            assignee_kind="agent",
            status="queued",
        ),
    ],
    activity=[
        ActivityEvent("1h ago", "Maya created mission — tasks queued, not yet dispatched", kind="human"),
    ],
    last_activity="1h ago",
)

MISSIONS: List[Mission] = [MISSION_1, MISSION_2, MISSION_3, MISSION_4]


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
