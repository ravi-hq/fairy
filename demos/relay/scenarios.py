"""
Pre-loaded scenes for the Relay demo.

Each scene is a list of (delay_seconds, event_kind, payload) tuples. The
runner in app.py walks the list, sleeps `delay_seconds` between events, and
broadcasts each event over the global SSE stream.

Event kinds:
  - "presence":      flip an agent's presence + activity tooltip
  - "message":       post a brand-new message into a channel
  - "message_update": patch an existing message (used to grow a streaming
                     agent reply token-by-token without spamming the timeline)
  - "thread_reply":  add a reply inside an existing message's thread
  - "typing":        show / hide the typing indicator at the bottom of a
                     channel
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Scene 1 — #on-call: PagerDuty alert fires, Scout investigates.
# ---------------------------------------------------------------------------

SCENE_ON_CALL_ALERT = [
    # PagerDuty bot drops the alert.
    (
        0.0,
        "message",
        {
            "channel": "on-call",
            "message": {
                "id": "m-pd-001",
                "author_id": "bot-pagerduty",
                "kind": "alert",
                "text": (
                    "**checkout-service: error rate 847/min** "
                    "(p99 latency 4.2s, baseline 180ms)\n"
                    "Triggered: just now · Severity: SEV-2 · "
                    "Service: checkout-service · Region: us-east-1"
                ),
            },
        },
    ),
    # Scout flips to working before anyone @-mentions it. This is the
    # "agents read the room" beat — the alert itself is the trigger.
    (
        1.2,
        "presence",
        {
            "agent_id": "scout",
            "status": "working",
            "activity": "investigating checkout-service alert",
        },
    ),
    (1.4, "typing", {"channel": "on-call", "agent_id": "scout", "on": True}),
    # Scout posts an empty investigation message that we'll grow over time.
    (
        2.0,
        "message",
        {
            "channel": "on-call",
            "message": {
                "id": "m-scout-001",
                "author_id": "scout",
                "kind": "agent_stream",
                "text": "On it — pulling metrics now.",
                "steps": [],
                "streaming": True,
            },
        },
    ),
    (
        3.4,
        "message_update",
        {
            "channel": "on-call",
            "message_id": "m-scout-001",
            "patch": {
                "steps": [
                    {
                        "label": "metrics",
                        "text": (
                            "querying datadog: checkout-service.errors "
                            "spiked from 6/min → 847/min at 14:32:11Z"
                        ),
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        5.0,
        "message_update",
        {
            "channel": "on-call",
            "message_id": "m-scout-001",
            "patch": {
                "steps": [
                    {
                        "label": "metrics",
                        "text": (
                            "querying datadog: checkout-service.errors "
                            "spiked from 6/min → 847/min at 14:32:11Z"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "deploys",
                        "text": (
                            "checking rollouts: v2.4.1 deployed to prod "
                            "at 14:29:04Z (3m 7s before spike) by maya"
                        ),
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        6.6,
        "message_update",
        {
            "channel": "on-call",
            "message_id": "m-scout-001",
            "patch": {
                "steps": [
                    {
                        "label": "metrics",
                        "text": (
                            "querying datadog: checkout-service.errors "
                            "spiked from 6/min → 847/min at 14:32:11Z"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "deploys",
                        "text": (
                            "checking rollouts: v2.4.1 deployed to prod "
                            "at 14:29:04Z (3m 7s before spike) by maya"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "traces",
                        "text": (
                            "sampling 50 failed traces: 47/50 timing out "
                            "in pgbouncer.acquire() — pool exhaustion"
                        ),
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        8.2,
        "message_update",
        {
            "channel": "on-call",
            "message_id": "m-scout-001",
            "patch": {
                "steps": [
                    {
                        "label": "metrics",
                        "text": (
                            "querying datadog: checkout-service.errors "
                            "spiked from 6/min → 847/min at 14:32:11Z"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "deploys",
                        "text": (
                            "checking rollouts: v2.4.1 deployed to prod "
                            "at 14:29:04Z (3m 7s before spike) by maya"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "traces",
                        "text": (
                            "sampling 50 failed traces: 47/50 timing out "
                            "in pgbouncer.acquire() — pool exhaustion"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "diff",
                        "text": (
                            "v2.4.1 diff: removed connection pool reuse "
                            "in CheckoutSession.complete() (commit a7f3e9)"
                        ),
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        9.4,
        "message_update",
        {
            "channel": "on-call",
            "message_id": "m-scout-001",
            "patch": {
                "text": "Found it. Posting brief in thread.",
                "streaming": False,
                "steps": [
                    {
                        "label": "metrics",
                        "text": (
                            "querying datadog: checkout-service.errors "
                            "spiked from 6/min → 847/min at 14:32:11Z"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "deploys",
                        "text": (
                            "checking rollouts: v2.4.1 deployed to prod "
                            "at 14:29:04Z (3m 7s before spike) by maya"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "traces",
                        "text": (
                            "sampling 50 failed traces: 47/50 timing out "
                            "in pgbouncer.acquire() — pool exhaustion"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "diff",
                        "text": (
                            "v2.4.1 diff: removed connection pool reuse "
                            "in CheckoutSession.complete() (commit a7f3e9)"
                        ),
                        "state": "done",
                    },
                    {
                        "label": "writeup",
                        "text": "drafting structured brief for thread",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (9.6, "typing", {"channel": "on-call", "agent_id": "scout", "on": False}),
    # Scout posts the structured brief as a thread reply on the alert.
    (
        10.0,
        "thread_reply",
        {
            "channel": "on-call",
            "parent_id": "m-pd-001",
            "message": {
                "id": "m-scout-brief-001",
                "author_id": "scout",
                "kind": "brief",
                "text": "Investigation brief — checkout-service SEV-2",
                "brief": {
                    "probable_cause": (
                        "v2.4.1 (deployed 14:29Z) removed the per-request "
                        "DB connection reuse in CheckoutSession.complete(). "
                        "Each checkout now acquires a new pgbouncer "
                        "connection and never releases it back to the pool."
                    ),
                    "evidence": (
                        "47/50 sampled error traces block in "
                        "pgbouncer.acquire() with wait_time > 4s. "
                        "Pool saturation at 100/100 since 14:32:08Z. "
                        "Diff a7f3e9 removed `with self.pool.connection()` "
                        "context manager."
                    ),
                    "blast_radius": (
                        "100% of checkout requests in us-east-1 affected "
                        "(approx 14k/min). eu-west-1 + ap-south-1 still "
                        "on v2.4.0 — unaffected. Cart and inventory "
                        "services healthy."
                    ),
                    "suggested_action": (
                        "Roll back checkout-service to v2.4.0 in us-east-1. "
                        "Hold the rollout to other regions. File a follow-up "
                        "to re-land a7f3e9 with the connection context "
                        "manager restored."
                    ),
                },
            },
        },
    ),
    # Scout drops back to idle.
    (
        11.0,
        "presence",
        {"agent_id": "scout", "status": "idle", "activity": "ready"},
    ),
]


# ---------------------------------------------------------------------------
# Scene 2 — #competitive-intel: competitor launch link → Researcher brief.
# ---------------------------------------------------------------------------

SCENE_COMPETITOR_LAUNCH = [
    (
        0.0,
        "message",
        {
            "channel": "competitive-intel",
            "message": {
                "id": "m-sam-001",
                "author_id": "sam",
                "kind": "user",
                "text": (
                    "did you see what notion shipped? "
                    "https://www.notion.so/blog/notion-agents-launch — "
                    "looks like they're going hard at our space"
                ),
            },
        },
    ),
    # Researcher reads the room — no @-mention needed.
    (
        1.5,
        "presence",
        {
            "agent_id": "researcher",
            "status": "working",
            "activity": "reading Notion launch post",
        },
    ),
    (
        2.6,
        "thread_reply",
        {
            "channel": "competitive-intel",
            "parent_id": "m-sam-001",
            "message": {
                "id": "m-researcher-001",
                "author_id": "researcher",
                "kind": "agent_stream",
                "text": "On it — fetching the launch post and pricing page now.",
                "steps": [],
                "streaming": True,
            },
        },
    ),
    (
        4.0,
        "message_update",
        {
            "channel": "competitive-intel",
            "message_id": "m-researcher-001",
            "patch": {
                "steps": [
                    {
                        "label": "fetch",
                        "text": "pulled launch post (1,840 words) + pricing + 3 demo videos",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        6.0,
        "message_update",
        {
            "channel": "competitive-intel",
            "message_id": "m-researcher-001",
            "patch": {
                "steps": [
                    {
                        "label": "fetch",
                        "text": "pulled launch post (1,840 words) + pricing + 3 demo videos",
                        "state": "done",
                    },
                    {
                        "label": "positioning",
                        "text": "comparing positioning vs prior 'Notion AI' framing",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        8.0,
        "message_update",
        {
            "channel": "competitive-intel",
            "message_id": "m-researcher-001",
            "patch": {
                "steps": [
                    {
                        "label": "fetch",
                        "text": "pulled launch post (1,840 words) + pricing + 3 demo videos",
                        "state": "done",
                    },
                    {
                        "label": "positioning",
                        "text": "comparing positioning vs prior 'Notion AI' framing",
                        "state": "done",
                    },
                    {
                        "label": "pricing",
                        "text": "$20/seat → $24/seat, new 'Agent credits' meter at $0.04/run",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        10.5,
        "message_update",
        {
            "channel": "competitive-intel",
            "message_id": "m-researcher-001",
            "patch": {
                "steps": [
                    {
                        "label": "fetch",
                        "text": "pulled launch post (1,840 words) + pricing + 3 demo videos",
                        "state": "done",
                    },
                    {
                        "label": "positioning",
                        "text": "comparing positioning vs prior 'Notion AI' framing",
                        "state": "done",
                    },
                    {
                        "label": "pricing",
                        "text": "$20/seat → $24/seat, new 'Agent credits' meter at $0.04/run",
                        "state": "done",
                    },
                    {
                        "label": "parity",
                        "text": "building feature parity matrix vs our shipped surface",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        12.0,
        "message_update",
        {
            "channel": "competitive-intel",
            "message_id": "m-researcher-001",
            "patch": {
                "text": "Brief ready.",
                "streaming": False,
                "steps": [
                    {
                        "label": "fetch",
                        "text": "pulled launch post (1,840 words) + pricing + 3 demo videos",
                        "state": "done",
                    },
                    {
                        "label": "positioning",
                        "text": "comparing positioning vs prior 'Notion AI' framing",
                        "state": "done",
                    },
                    {
                        "label": "pricing",
                        "text": "$20/seat → $24/seat, new 'Agent credits' meter at $0.04/run",
                        "state": "done",
                    },
                    {
                        "label": "parity",
                        "text": "building feature parity matrix vs our shipped surface",
                        "state": "done",
                    },
                    {
                        "label": "synth",
                        "text": "drafting roadmap implications",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        12.4,
        "thread_reply",
        {
            "channel": "competitive-intel",
            "parent_id": "m-sam-001",
            "message": {
                "id": "m-researcher-brief-001",
                "author_id": "researcher",
                "kind": "competitor_brief",
                "text": "Notion Agents launch — competitive read",
                "competitor_brief": {
                    "positioning_shift": (
                        "Notion is dropping 'AI assistant' framing entirely "
                        "and rebranding around 'Agents that work in your "
                        "workspace.' First time they've used the word "
                        "'teammate' in marketing copy. Aimed squarely at the "
                        "category we're trying to define."
                    ),
                    "pricing_changes": (
                        "Plus seat: $20 → $24/mo. New 'Agent credits' "
                        "metered SKU at $0.04/run, 500 free credits/mo per "
                        "seat. Enterprise gets unlimited credits. This is "
                        "the first per-action meter Notion has shipped."
                    ),
                    "feature_parity": [
                        {"feature": "Persistent agent sessions", "us": "yes", "them": "no — stateless per request"},
                        {"feature": "Agent presence in channels", "us": "yes (this product)", "them": "no — workspace only"},
                        {"feature": "Multi-step tool use", "us": "yes", "them": "yes (new)"},
                        {"feature": "Native filesystem", "us": "yes (Sprites)", "them": "no"},
                        {"feature": "BYO model", "us": "yes (Claude/Codex/Gemini)", "them": "no — GPT-5 only"},
                    ],
                    "implications": [
                        "The 'agent as teammate' frame is now contested — we should ship the presence + DM-an-agent story this quarter, not next.",
                        "Their per-run meter validates usage-based pricing; we should publish our own pricing this month before they anchor it.",
                        "Persistence + native filesystem is our durable wedge — every demo should lead with 'the agent's workspace stays warm.'",
                    ],
                },
            },
        },
    ),
    (
        13.2,
        "presence",
        {
            "agent_id": "researcher",
            "status": "idle",
            "activity": "ready",
        },
    ),
]


# ---------------------------------------------------------------------------
# Scene 3 — #engineering: Migrator hits a permission wall, pings @jake.
# ---------------------------------------------------------------------------

SCENE_MIGRATION_PERMISSION = [
    (
        0.0,
        "message",
        {
            "channel": "engineering",
            "message": {
                "id": "m-maya-001",
                "author_id": "maya",
                "kind": "user",
                "text": (
                    "@migrator — kick off the prod migration when you're "
                    "ready. drop_legacy_email_verified should be safe but "
                    "double-check the pre-flight before you actually run it"
                ),
            },
        },
    ),
    (
        1.4,
        "presence",
        {
            "agent_id": "migrator",
            "status": "working",
            "activity": "running pre-flight checks",
        },
    ),
    (
        2.0,
        "message",
        {
            "channel": "engineering",
            "message": {
                "id": "m-migrator-001",
                "author_id": "migrator",
                "kind": "agent_stream",
                "text": "Picking it up. Running pre-flight scan first.",
                "steps": [],
                "streaming": True,
            },
        },
    ),
    (
        4.0,
        "message_update",
        {
            "channel": "engineering",
            "message_id": "m-migrator-001",
            "patch": {
                "steps": [
                    {
                        "label": "schema",
                        "text": "loaded schema for `users` table (24 cols, 14.2M rows)",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        5.4,
        "message_update",
        {
            "channel": "engineering",
            "message_id": "m-migrator-001",
            "patch": {
                "steps": [
                    {
                        "label": "schema",
                        "text": "loaded schema for `users` table (24 cols, 14.2M rows)",
                        "state": "done",
                    },
                    {
                        "label": "usage",
                        "text": "scanned codebase: 0 references to `email_verified` in src/",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        6.8,
        "message_update",
        {
            "channel": "engineering",
            "message_id": "m-migrator-001",
            "patch": {
                "steps": [
                    {
                        "label": "schema",
                        "text": "loaded schema for `users` table (24 cols, 14.2M rows)",
                        "state": "done",
                    },
                    {
                        "label": "usage",
                        "text": "scanned codebase: 0 references to `email_verified` in src/",
                        "state": "done",
                    },
                    {
                        "label": "reads",
                        "text": "queried pg_stat_statements: 0 reads of users.email_verified in the last 30 days",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        8.2,
        "message_update",
        {
            "channel": "engineering",
            "message_id": "m-migrator-001",
            "patch": {
                "steps": [
                    {
                        "label": "schema",
                        "text": "loaded schema for `users` table (24 cols, 14.2M rows)",
                        "state": "done",
                    },
                    {
                        "label": "usage",
                        "text": "scanned codebase: 0 references to `email_verified` in src/",
                        "state": "done",
                    },
                    {
                        "label": "reads",
                        "text": "queried pg_stat_statements: 0 reads of users.email_verified in the last 30 days",
                        "state": "done",
                    },
                    {
                        "label": "backup",
                        "text": "verified pre-migration snapshot exists (rds:prod-2026-04-29-1407)",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        9.6,
        "message_update",
        {
            "channel": "engineering",
            "message_id": "m-migrator-001",
            "patch": {
                "text": "Pre-flight clean. Pausing for human sign-off before I touch prod.",
                "streaming": False,
                "steps": [
                    {
                        "label": "schema",
                        "text": "loaded schema for `users` table (24 cols, 14.2M rows)",
                        "state": "done",
                    },
                    {
                        "label": "usage",
                        "text": "scanned codebase: 0 references to `email_verified` in src/",
                        "state": "done",
                    },
                    {
                        "label": "reads",
                        "text": "queried pg_stat_statements: 0 reads of users.email_verified in the last 30 days",
                        "state": "done",
                    },
                    {
                        "label": "backup",
                        "text": "verified pre-migration snapshot exists (rds:prod-2026-04-29-1407)",
                        "state": "done",
                    },
                ],
            },
        },
    ),
    (
        9.8,
        "presence",
        {
            "agent_id": "migrator",
            "status": "awaiting_human",
            "activity": "waiting on @jake to approve column drop",
        },
    ),
    # The careful junior engineer ping. Real SQL, real numbers, real ask.
    (
        10.2,
        "message",
        {
            "channel": "engineering",
            "message": {
                "id": "m-migrator-002",
                "author_id": "migrator",
                "kind": "approval_request",
                "text": (
                    "@jake — pre-flight is clean and I want to proceed, but "
                    "this is a destructive change so I'd like an explicit "
                    "human sign-off before I run it.\n\n"
                    "**About to execute:**\n"
                    "```sql\n"
                    "BEGIN;\n"
                    "ALTER TABLE users DROP COLUMN email_verified;\n"
                    "COMMIT;\n"
                    "```\n\n"
                    "**What I checked:**\n"
                    "• 0 references to `email_verified` in `src/` "
                    "(searched 1,247 files)\n"
                    "• 0 reads of the column via `pg_stat_statements` "
                    "in the last 30 days\n"
                    "• Snapshot `rds:prod-2026-04-29-1407` is "
                    "verified and restorable\n"
                    "• Estimated lock time on 14.2M rows: ~340ms "
                    "(acceptable for this table's traffic)\n\n"
                    "**What I'm not sure about:**\n"
                    "• Maya said this should be safe, but I can't tell "
                    "whether any external integrations (Stripe webhooks, "
                    "the marketing CRM sync, the support tool) read this "
                    "column out-of-band. That's outside the codebase I "
                    "have access to.\n\n"
                    "Reply ✅ to proceed or ❌ to abort. "
                    "I'll wait."
                ),
                "approval": {
                    "destructive": True,
                    "options": [
                        {"label": "Proceed", "key": "proceed", "tone": "danger"},
                        {"label": "Abort", "key": "abort", "tone": "neutral"},
                    ],
                },
            },
        },
    ),
]


SCENES = {
    "on-call-alert": {
        "id": "on-call-alert",
        "label": "#on-call — PagerDuty alert fires",
        "channel": "on-call",
        "events": SCENE_ON_CALL_ALERT,
    },
    "competitor-launch": {
        "id": "competitor-launch",
        "label": "#competitive-intel — Competitor launch",
        "channel": "competitive-intel",
        "events": SCENE_COMPETITOR_LAUNCH,
    },
    "migration-permission": {
        "id": "migration-permission",
        "label": "#engineering — Stuck on permission",
        "channel": "engineering",
        "events": SCENE_MIGRATION_PERMISSION,
    },
}


# ---------------------------------------------------------------------------
# Agent responder engine — drives @-mentions, slash commands, agent-DMs.
#
# Shape: AGENT_RESPONDERS[agent_id] = list of dicts with:
#   keywords: list of substrings — match on lowercased prompt; empty = fallback
#   activity: presence subline while working
#   steps:    list of {label, text} — one step per ~1.2s of streaming
#   reply:    final text or {kind: "...", ...} payload posted after the stream
# ---------------------------------------------------------------------------

AGENT_RESPONDERS: dict[str, list[dict]] = {
    "scout": [
        {
            "keywords": ["502", "5xx", "alert", "incident", "spike", "errors"],
            "activity": "investigating error spike",
            "steps": [
                {"label": "metrics", "text": "pulling datadog: error rate, p99 latency, region split"},
                {"label": "deploys", "text": "checking last 60min of rollouts across affected services"},
                {"label": "traces", "text": "sampling 30 failed traces for common stack frame"},
                {"label": "writeup", "text": "drafting incident brief"},
            ],
            "reply": {
                "kind": "brief",
                "text": "Quick-look brief — error spike",
                "brief": {
                    "probable_cause": (
                        "Spike correlates with the v2.4.1 rollout 3 minutes prior. "
                        "47 of 50 sampled traces block in the connection acquire path."
                    ),
                    "evidence": (
                        "datadog: errors 6/min → 847/min at 14:32. "
                        "pgbouncer pool saturation 100/100 since 14:32:08Z."
                    ),
                    "blast_radius": (
                        "us-east-1 only. eu-west-1 + ap-south-1 still on v2.4.0 — clean."
                    ),
                    "suggested_action": (
                        "Roll back checkout-service to v2.4.0 in us-east-1. "
                        "I can draft the rollback PR if you want — say the word."
                    ),
                },
            },
        },
        {
            "keywords": ["deploy", "rollout", "release"],
            "activity": "diffing recent deploys",
            "steps": [
                {"label": "fetch", "text": "listing the last 10 deploys across prod services"},
                {"label": "diff", "text": "diffing v2.4.1 → v2.4.0 of checkout-service"},
                {"label": "owners", "text": "tagging owners of touched files"},
                {"label": "writeup", "text": "summarizing"},
            ],
            "reply": (
                "Last hour:\n"
                "• checkout-service v2.4.1 (maya, 14:29Z) — touches CheckoutSession.complete\n"
                "• billing-service v3.1.2 (rohit, 13:51Z) — small copy fix\n"
                "• marketing-site (auto, 13:02Z) — content sync\n\n"
                "Nothing else risky in the window."
            ),
        },
        {
            "keywords": ["latency", "slow", "p99", "p95"],
            "activity": "auditing latency",
            "steps": [
                {"label": "metrics", "text": "pulling p50/p95/p99 across the service mesh"},
                {"label": "outliers", "text": "ranking endpoints by latency delta vs 24h baseline"},
                {"label": "writeup", "text": "noting the worst offenders"},
            ],
            "reply": (
                "p99 right now (last 5m):\n"
                "• checkout/complete: 4.2s (baseline 180ms) ← issue\n"
                "• cart/add: 92ms (steady)\n"
                "• search/query: 240ms (steady)\n"
                "Only checkout is anomalous."
            ),
        },
        {
            "keywords": ["log", "logs", "exception", "stacktrace"],
            "activity": "scanning logs",
            "steps": [
                {"label": "query", "text": "tailing recent error logs across services"},
                {"label": "cluster", "text": "grouping by stack-frame fingerprint"},
                {"label": "writeup", "text": "summarizing top clusters"},
            ],
            "reply": (
                "Top 3 error clusters in the last 15min:\n"
                "1. `pgbouncer.acquire timeout` — 412 events (checkout-service)\n"
                "2. `JSONDecodeError on /webhook/stripe` — 7 events (billing-service)\n"
                "3. `403 from auth/refresh` — 4 events (gateway)\n\n"
                "Cluster 1 is dominant. Want me to investigate it?"
            ),
        },
        {
            "keywords": ["health", "status", "everything ok", "all good"],
            "activity": "running a fast health sweep",
            "steps": [
                {"label": "uptime", "text": "checking prod service uptime"},
                {"label": "pagerduty", "text": "checking active incidents"},
                {"label": "writeup", "text": "summarizing"},
            ],
            "reply": (
                "Health snapshot:\n"
                "• 0 active SEV incidents\n"
                "• All prod services 99.9%+ uptime over the last 24h\n"
                "• 1 warning: pgbouncer pool utilization on checkout-service trending up\n"
                "I'll keep an eye on the warning."
            ),
        },
        # Fallback.
        {
            "keywords": [],
            "activity": "looking into it",
            "steps": [
                {"label": "context", "text": "reading the question + recent channel context"},
                {"label": "search", "text": "checking metrics, deploys, and recent alerts"},
                {"label": "writeup", "text": "drafting a quick read"},
            ],
            "reply": (
                "Nothing immediately popping. If you can point me at a specific service, "
                "alert, or time range I'll dig deeper."
            ),
        },
    ],
    "researcher": [
        {
            "keywords": ["linear", "notion", "competitor", "competitive"],
            "activity": "scanning competitor surface",
            "steps": [
                {"label": "fetch", "text": "pulling latest changelog + release notes"},
                {"label": "compare", "text": "diffing positioning + pricing vs last week"},
                {"label": "synth", "text": "drafting brief"},
            ],
            "reply": {
                "kind": "competitor_brief",
                "text": "Competitor pulse — last 7 days",
                "competitor_brief": {
                    "positioning_shift": (
                        "Linear leaning harder into 'agents-as-features-in-Linear' framing — still "
                        "no presence model, still stateless, still issue-tracker-shaped."
                    ),
                    "pricing_changes": (
                        "No new pricing this week. Their per-action meter is still in private beta."
                    ),
                    "feature_parity": [
                        {"feature": "Persistent agent sessions", "us": "yes", "them": "no"},
                        {"feature": "Agent presence in channels", "us": "yes", "them": "no"},
                        {"feature": "Multi-step tool use", "us": "yes", "them": "yes"},
                        {"feature": "BYO model", "us": "yes", "them": "no"},
                    ],
                    "implications": [
                        "Our presence + persistence wedge is still uncontested.",
                        "We should publish a pricing page before they leave private beta.",
                    ],
                },
            },
        },
        {
            "keywords": ["pricing", "price", "monetize"],
            "activity": "researching pricing",
            "steps": [
                {"label": "scan", "text": "pulling published pricing for top 6 comparables"},
                {"label": "model", "text": "running a per-agent meter sensitivity"},
                {"label": "writeup", "text": "summarizing"},
            ],
            "reply": (
                "Pricing landscape (per seat/mo for the closest tier):\n"
                "• Linear: $14 — no agent meter\n"
                "• Notion: $24 — $0.04/agent run, 500 free\n"
                "• Slack: $12.50 — no agent meter (yet)\n"
                "• ClickUp: $19 — no agent meter\n"
                "• us: ⟨ no public pricing ⟩\n\n"
                "If we anchor at $25/seat + $0.05/run, our cost-of-goods at projected mix is ~37%. "
                "Want me to draft a pricing page?"
            ),
        },
        {
            "keywords": ["okr", "metrics", "growth", "funnel"],
            "activity": "pulling funnel data",
            "steps": [
                {"label": "query", "text": "running this week's funnel against last week"},
                {"label": "compare", "text": "diffing step conversion deltas"},
                {"label": "writeup", "text": "highlighting the biggest movers"},
            ],
            "reply": (
                "This week's funnel:\n"
                "• Signup → first agent: 41% (last week 38%, +3pt)\n"
                "• First agent → second agent: 22% (last week 24%, -2pt)\n"
                "• Day-7 retention: 31% (last week 29%, +2pt)\n\n"
                "Step 2 dip is the thing to watch. I'll dig if you want."
            ),
        },
        {
            "keywords": ["customer", "feedback", "interview"],
            "activity": "scanning customer feedback",
            "steps": [
                {"label": "fetch", "text": "pulling last 30 days of support + sales-call notes"},
                {"label": "cluster", "text": "tagging recurring themes"},
                {"label": "writeup", "text": "summarizing"},
            ],
            "reply": (
                "Top themes from the last 30 days of customer touchpoints:\n"
                "1. 'I want my agent to remember what it did yesterday' — 14 mentions\n"
                "2. 'Slack-style presence would be huge' — 9 mentions\n"
                "3. Pricing predictability concerns — 7 mentions\n"
                "4. Onboarding friction (env var step) — 6 mentions\n\n"
                "(1) and (2) basically describe this product. We should lead the next demo with them."
            ),
        },
        {
            "keywords": ["roadmap", "plan", "quarter", "q2", "q3"],
            "activity": "synthesizing roadmap input",
            "steps": [
                {"label": "context", "text": "pulling current roadmap doc + recent strategy threads"},
                {"label": "compare", "text": "checking deltas vs competitor moves"},
                {"label": "writeup", "text": "drafting a recommendation"},
            ],
            "reply": (
                "Roadmap pulse:\n"
                "• Persistence + presence still our durable wedge — keep at the top.\n"
                "• Pricing page is the highest-leverage non-product item this quarter.\n"
                "• Customer-asked 'agent memory' lines up with the persistence story — surface it as a feature, not just an architecture detail.\n\n"
                "Want me to draft a one-pager?"
            ),
        },
        # Fallback.
        {
            "keywords": [],
            "activity": "researching the question",
            "steps": [
                {"label": "scope", "text": "interpreting the ask"},
                {"label": "fetch", "text": "pulling relevant sources"},
                {"label": "synth", "text": "drafting a brief"},
            ],
            "reply": (
                "I can dig deeper if you give me a specific company, market, or metric to focus on. "
                "For broad questions I'll pull from our internal docs + the public web."
            ),
        },
    ],
    "migrator": [
        {
            "keywords": ["dry run", "dry-run", "preflight", "pre-flight", "v3", "schema"],
            "activity": "running pre-flight",
            "steps": [
                {"label": "schema", "text": "loading current vs target schema"},
                {"label": "usage", "text": "scanning codebase for column references"},
                {"label": "reads", "text": "querying pg_stat_statements for live reads"},
                {"label": "backup", "text": "verifying snapshot exists"},
            ],
            "reply": {
                "kind": "migration_report",
                "text": "Pre-flight report — v3 schema dry-run",
                "migration": {
                    "summary": "4 changes, 0 destructive, 0 blockers. Estimated total lock time 1.1s.",
                    "changes": [
                        {"op": "ADD COLUMN", "target": "users.preferred_locale", "rows": "14.2M", "lock_ms": 280, "risk": "low"},
                        {"op": "ADD INDEX", "target": "orders(created_at, status)", "rows": "84.7M", "lock_ms": 450, "risk": "low"},
                        {"op": "BACKFILL", "target": "users.preferred_locale ← 'en'", "rows": "14.2M", "lock_ms": 0, "risk": "low"},
                        {"op": "ALTER TYPE", "target": "orders.amount_cents → BIGINT", "rows": "84.7M", "lock_ms": 380, "risk": "medium"},
                    ],
                },
            },
        },
        {
            "keywords": ["drop", "delete", "destructive", "remove column"],
            "activity": "checking for destructive changes",
            "steps": [
                {"label": "scan", "text": "looking for DROP / TRUNCATE / DELETE in pending migrations"},
                {"label": "usage", "text": "verifying nothing reads the targets"},
                {"label": "writeup", "text": "drafting the human sign-off prompt"},
            ],
            "reply": (
                "I see destructive operations in the pending migration set. "
                "I won't run them without explicit human sign-off — drop a 👍 here or "
                "use `/migrator preflight <table>` and I'll show you the exact SQL first."
            ),
        },
        {
            "keywords": ["index", "indexes", "performance"],
            "activity": "auditing indexes",
            "steps": [
                {"label": "scan", "text": "listing missing indexes flagged by pg_stat_user_indexes"},
                {"label": "rank", "text": "ranking by query frequency × cost"},
                {"label": "writeup", "text": "summarizing"},
            ],
            "reply": (
                "Top 3 missing-index candidates:\n"
                "1. `orders(user_id, created_at)` — used by /orders.list, ~14k QPS\n"
                "2. `events(session_id)` — used by /sessions/replay, ~2k QPS\n"
                "3. `users(email_lower)` — used by login, ~800 QPS\n\n"
                "(1) is the obvious win. I can prepare the migration if you want."
            ),
        },
        {
            "keywords": ["backup", "snapshot", "restore"],
            "activity": "checking backup state",
            "steps": [
                {"label": "rds", "text": "listing the last 7 RDS snapshots"},
                {"label": "verify", "text": "verifying the most recent snapshot is restorable"},
            ],
            "reply": (
                "Last verified snapshot: `rds:prod-2026-04-29-1407` (4h ago, restorable in ~22min). "
                "Daily snapshots are healthy for the last 7 days."
            ),
        },
        # Fallback.
        {
            "keywords": [],
            "activity": "thinking about the migration",
            "steps": [
                {"label": "context", "text": "reading the ask"},
                {"label": "search", "text": "checking pending migrations + recent ones"},
                {"label": "writeup", "text": "drafting a response"},
            ],
            "reply": (
                "I can run a pre-flight for any table — try `/migrator preflight users` or "
                "@-mention me with a more specific request."
            ),
        },
    ],
    "pr-bot": [
        {
            "keywords": ["review", "look at", "pr ", "pr#", "#1284", "#"],
            "activity": "reviewing PR",
            "steps": [
                {"label": "fetch", "text": "pulling diff + linked issue"},
                {"label": "lint", "text": "running static analysis on touched files"},
                {"label": "test", "text": "checking CI signal"},
                {"label": "writeup", "text": "drafting the review"},
            ],
            "reply": {
                "kind": "pr_review",
                "text": "PR review — #1284 (auto)",
                "pr_review": {
                    "summary": "+184 / -47 across 9 files. Two approvals already. Looks landable; flagged a couple of nits.",
                    "findings": [
                        {"severity": "blocking", "count": 0},
                        {"severity": "warning", "count": 2},
                        {"severity": "nit", "count": 3},
                    ],
                    "highlights": [
                        "warn: `CheckoutSession.complete()` no longer reuses the connection — confirm that's intentional after the on-call incident.",
                        "warn: removed test `test_checkout_idempotency_on_retry` — not replaced. Worth keeping.",
                        "nit: 3 places in `billing/refund.py` still use `dict()` instead of `{}` literal.",
                    ],
                },
            },
        },
        {
            "keywords": ["test", "coverage", "ci"],
            "activity": "checking CI / coverage",
            "steps": [
                {"label": "ci", "text": "listing recent CI runs"},
                {"label": "coverage", "text": "computing coverage delta vs main"},
                {"label": "writeup", "text": "summarizing"},
            ],
            "reply": (
                "Last 24h CI:\n"
                "• 18 runs, 17 green, 1 flake (retried, passed)\n"
                "• Coverage on main: 84.3% (-0.4pt vs yesterday — checkout-service refactor pulled some tests)\n\n"
                "Worth re-adding a couple of the retired tests."
            ),
        },
        {
            "keywords": ["lint", "style"],
            "activity": "running the linter",
            "steps": [
                {"label": "lint", "text": "running ruff + tsc across the diff"},
                {"label": "writeup", "text": "summarizing"},
            ],
            "reply": (
                "Lint clean across the diff. 0 errors, 4 nits (auto-fixable). Want me to push the fixup commit?"
            ),
        },
        # Fallback.
        {
            "keywords": [],
            "activity": "looking",
            "steps": [
                {"label": "context", "text": "reading the question"},
                {"label": "search", "text": "checking recent PRs + CI signal"},
                {"label": "writeup", "text": "drafting a response"},
            ],
            "reply": (
                "Point me at a PR number (e.g. `@prbot review #1284`) and I'll do a full pass."
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# Agent profiles — clicking a name opens a modal with this data.
# ---------------------------------------------------------------------------

AGENT_PROFILES: dict[str, dict] = {
    "scout": {
        "id": "scout",
        "name": "Scout",
        "kind": "agent",
        "role": "On-call investigations",
        "made_in": "Forge",
        "system_prompt": (
            "You are Scout, the on-call investigations agent for Acme Eng. When an alert "
            "fires or a teammate asks about a production issue, you read the alert, pull "
            "metrics from datadog, check recent rollouts, sample failed traces, and post a "
            "structured brief: probable cause, evidence, blast radius, suggested action. "
            "You never propose destructive remediations without a human in the loop. You "
            "prefer being concise over being thorough — the on-call engineer is reading you "
            "while paged at 2am."
        ),
        "tools": ["datadog.query", "github.diff", "kubectl.logs", "tracing.sample", "pagerduty.read"],
        "channels": ["on-call", "engineering"],
        "recent_activity": [
            {"channel": "on-call", "text": "Investigation brief — checkout-service SEV-2", "ts_offset_min": -34},
            {"channel": "on-call", "text": "Health snapshot: all green, 1 warning on pgbouncer pool", "ts_offset_min": -240},
            {"channel": "engineering", "text": "Diffed v2.4.0 → v2.4.1 — flagged the connection-reuse change", "ts_offset_min": -480},
            {"channel": "on-call", "text": "Resolved SEV-3 (gateway 503s) — root cause was a stale CDN config", "ts_offset_min": -1200},
            {"channel": "engineering", "text": "Posted top error clusters from yesterday's logs", "ts_offset_min": -1440},
            {"channel": "on-call", "text": "Last 24h: 0 SEV-1, 1 SEV-3 (resolved)", "ts_offset_min": -1640},
        ],
    },
    "researcher": {
        "id": "researcher",
        "name": "Researcher",
        "kind": "agent",
        "role": "Market & competitive intel",
        "made_in": "Forge",
        "system_prompt": (
            "You are Researcher, Acme Eng's market and competitive intel agent. You scan "
            "competitor changelogs, pricing pages, and launch posts; you pull customer "
            "feedback themes from support and sales-call notes; and you synthesize crisp "
            "briefs that lead with the so-what for our roadmap. You never bury the lede. "
            "When the question is fuzzy, you ask one clarifying question before going deep."
        ),
        "tools": ["web.fetch", "intercom.search", "gong.search", "notion.read", "stripe.read"],
        "channels": ["competitive-intel", "growth", "general"],
        "recent_activity": [
            {"channel": "competitive-intel", "text": "Notion Agents launch — competitive read", "ts_offset_min": -50},
            {"channel": "growth", "text": "First read on the funnel dip — likely the new env-var step", "ts_offset_min": -354},
            {"channel": "general", "text": "Weekly competitive scan went out", "ts_offset_min": -90},
            {"channel": "competitive-intel", "text": "Linear Agents writeup — short version: not a threat yet", "ts_offset_min": -1440},
            {"channel": "growth", "text": "Activation funnel dropped 41 → 38% on first-agent-spawn", "ts_offset_min": -2880},
            {"channel": "competitive-intel", "text": "Pricing landscape pulse — 6 comparables", "ts_offset_min": -4320},
        ],
    },
    "migrator": {
        "id": "migrator",
        "name": "Migrator",
        "kind": "agent",
        "role": "Database operations",
        "made_in": "Forge",
        "system_prompt": (
            "You are Migrator, the database operations agent for Acme Eng. You run "
            "pre-flight checks before any schema change (load schema, scan codebase usage, "
            "check pg_stat_statements, verify backups), and you NEVER run a destructive "
            "operation without explicit human sign-off. When you ask for sign-off, you "
            "show the exact SQL, what you checked, and what you couldn't verify. You "
            "behave like a careful junior engineer: thorough, transparent, conservative."
        ),
        "tools": ["postgres.query", "rds.snapshot", "github.code_search", "pg_stat.read"],
        "channels": ["engineering"],
        "recent_activity": [
            {"channel": "engineering", "text": "Pre-flight clean for drop_legacy_email_verified — awaiting @jake", "ts_offset_min": -22},
            {"channel": "engineering", "text": "Pre-flight report — v3 schema dry-run (4 changes, 0 destructive)", "ts_offset_min": -3000},
            {"channel": "engineering", "text": "Last verified snapshot: rds:prod-2026-04-29-0207", "ts_offset_min": -4000},
            {"channel": "engineering", "text": "Top 3 missing-index candidates — orders(user_id, created_at) is the win", "ts_offset_min": -5000},
            {"channel": "engineering", "text": "Migration drop_unused_columns_2026q1 ran clean (lock 280ms)", "ts_offset_min": -8640},
            {"channel": "engineering", "text": "Pre-flight clean for add_users_preferred_locale", "ts_offset_min": -10080},
        ],
    },
    "pr-bot": {
        "id": "pr-bot",
        "name": "PR Bot",
        "kind": "agent",
        "role": "Pull request review summaries",
        "made_in": "Forge",
        "system_prompt": (
            "You are PR Bot. For every PR you're asked to review (or are auto-assigned "
            "to), you pull the diff, run static analysis, check CI signal, and produce a "
            "structured review: summary, blocking issues, warnings, nits. You skip the "
            "obvious — your goal is to make the human reviewer's life easier, not "
            "duplicate what their eyes will catch."
        ),
        "tools": ["github.pulls.get", "github.checks.list", "ruff.run", "tsc.run", "coverage.diff"],
        "channels": ["engineering", "general"],
        "recent_activity": [
            {"channel": "engineering", "text": "PR review — #1284 (auto): 2 warnings, 3 nits, landable", "ts_offset_min": -90},
            {"channel": "engineering", "text": "PR #4821 summary: removes per-request DB connection reuse", "ts_offset_min": -42},
            {"channel": "general", "text": "Yesterday: 14 PRs reviewed, 12 landed, 2 still open", "ts_offset_min": -1440},
            {"channel": "engineering", "text": "PR #4807 nit: missing tests for the refund branch", "ts_offset_min": -2880},
            {"channel": "engineering", "text": "Coverage on main: 84.3% (-0.4pt — checkout refactor pulled tests)", "ts_offset_min": -4000},
            {"channel": "engineering", "text": "Lint clean across the week's PRs", "ts_offset_min": -7200},
        ],
    },
}


# Profiles for humans, used by the same modal.
HUMAN_PROFILES: dict[str, dict] = {
    "jake": {
        "id": "jake", "name": "Jake G", "kind": "human", "role": "Founder",
        "bio": "Building Acme Eng. Reachable here or by carrier pigeon.",
        "channels": ["general", "engineering", "on-call", "competitive-intel", "growth"],
    },
    "maya": {
        "id": "maya", "name": "Maya M", "kind": "human", "role": "Staff Engineer",
        "bio": "Owns checkout + billing services. On call this week.",
        "channels": ["general", "engineering", "on-call"],
    },
    "sam": {
        "id": "sam", "name": "Sam P", "kind": "human", "role": "Head of Product",
        "bio": "Q2 planning, pricing, competitive watch.",
        "channels": ["general", "competitive-intel", "growth"],
    },
}


# ---------------------------------------------------------------------------
# Pre-populated DM threads.
#
# Each entry: id, with (list of member ids, the human-side is implicit), label,
# and seed messages with ts_offset_min anchors.
# ---------------------------------------------------------------------------

SEED_DMS: dict[str, dict] = {
    "dm-scout": {
        "id": "dm-scout",
        "with": ["scout"],
        "label": "Scout",
        "messages": [
            {"author_id": "jake", "text": "hey can you check if anything weird happened on prod last night around 2am?", "ts_offset_min": -2880},
            {"author_id": "scout", "text": "On it.", "ts_offset_min": -2879},
            {
                "author_id": "scout",
                "kind": "brief",
                "text": "Quick look — last night around 02:00Z",
                "brief": {
                    "probable_cause": (
                        "A 4-minute uptick in 5xx on the gateway at 02:14Z. "
                        "Caused by a transient CDN config sync — auto-resolved at 02:18Z."
                    ),
                    "evidence": (
                        "datadog: gateway.5xx 12/min → 142/min at 02:14, back to 14/min at 02:18. "
                        "No human pages fired. No customer-visible failure outside the window."
                    ),
                    "blast_radius": (
                        "All regions briefly. ~4,200 affected requests over 4 minutes."
                    ),
                    "suggested_action": (
                        "Nothing immediate. I'd suggest filing a follow-up to make CDN config "
                        "sync atomic — happy to draft if you want."
                    ),
                },
                "ts_offset_min": -2876,
            },
            {"author_id": "jake", "text": "nice — yeah file the followup, low priority", "ts_offset_min": -2870},
        ],
    },
    "dm-researcher": {
        "id": "dm-researcher",
        "with": ["researcher"],
        "label": "Researcher",
        "messages": [
            {"author_id": "jake", "text": "give me a 1-line read on whether figma's launch yesterday changes anything for us", "ts_offset_min": -1440},
            {"author_id": "researcher", "text": "On it — 30 seconds.", "ts_offset_min": -1439},
            {"author_id": "researcher", "text": "Short answer: no. Figma's launch is design-tool-shaped (an agent inside their canvas) — they're not contesting the agent-as-teammate frame. We're still uncontested on presence + persistence + multi-channel. Long version in #competitive-intel if you want.", "ts_offset_min": -1438},
        ],
    },
    "dm-maya": {
        "id": "dm-maya",
        "with": ["maya"],
        "label": "Maya M",
        "messages": [
            {"author_id": "maya", "text": "moving the project sync to thursday this week — works for you?", "ts_offset_min": -360},
            {"author_id": "jake", "text": "yep, thursday is fine", "ts_offset_min": -358},
            {"author_id": "maya", "text": "great — also, the v3 schema work is going to slip a couple days. I'll have migrator do a dry run by EOD wed so we have data for the sync", "ts_offset_min": -355},
            {"author_id": "jake", "text": "all good. how's on-call going?", "ts_offset_min": -350},
            {"author_id": "maya", "text": "quiet so far 🤞", "ts_offset_min": -349},
        ],
    },
    "dm-sam": {
        "id": "dm-sam",
        "with": ["sam"],
        "label": "Sam P",
        "messages": [],
    },
}


# ---------------------------------------------------------------------------
# Seed history — populates each channel with realistic backstory.
# ---------------------------------------------------------------------------

SEED_HISTORY: dict[str, list[dict]] = {
    "general": [
        {"author_id": "sam", "text": "morning everyone — quiet week target: zero unplanned interruptions ✋", "ts_offset_min": -540},
        {"author_id": "maya", "text": "morning ☕", "ts_offset_min": -535},
        {"author_id": "jake", "text": "shipping the new docs landing today, eyes welcome", "ts_offset_min": -510},
        {"author_id": "researcher", "text": "Weekly competitive scan: nothing earth-shaking from the top 6 this week. Full writeup in #competitive-intel. Linear shipped a small UI thing, that's about it.", "ts_offset_min": -480},
        {"author_id": "pr-bot", "text": "Yesterday's review queue: 14 PRs, 12 landed, 2 still open (#4801 and #4815). Both are low-risk.", "ts_offset_min": -360},
        {"author_id": "sam", "text": "Reminder: Q2 planning doc is due Friday. Drop comments by EOD Wed.", "ts_offset_min": -180},
        {"author_id": "maya", "text": "lunch run anyone? thinking that ramen place by the office", "ts_offset_min": -120},
        {"author_id": "jake", "text": "in", "ts_offset_min": -118},
        {"author_id": "researcher", "text": "Heads up — three things worth a look in #competitive-intel from this morning's scan.", "ts_offset_min": -90},
        {"author_id": "maya", "text": "checkout-service v2.4.1 deployed clean to prod 🎉", "ts_offset_min": -60},
    ],
    "engineering": [
        {"author_id": "maya", "text": "anyone seen the intermittent flake on `test_session_resume_after_idle`? it's failed 3x in the last 50 runs but I can't repro locally", "ts_offset_min": -1440},
        {"author_id": "jake", "text": "haven't, do you have a CI link?", "ts_offset_min": -1430},
        {"author_id": "maya", "text": "yeah https://ci.acme.dev/run/8820 — happens on the linux-arm64 runner only, intel is clean", "ts_offset_min": -1420},
        {"author_id": "pr-bot", "text": "PR #4821 summary: removes per-request DB connection reuse in CheckoutSession.complete. Net -34/+12 lines. Two approvals from @scout (auto) and @maya.", "ts_offset_min": -90},
        {"author_id": "maya", "text": "Heads up — landing the checkout-service v2.4.1 rollout in ~10. PR #4821, two approvals.", "ts_offset_min": -45},
        {"author_id": "maya", "text": "deploy out, all green ✅", "ts_offset_min": -16},
    ],
    "on-call": [
        {"author_id": "scout", "text": "Last 24h: 0 SEV-1, 1 SEV-3 (resolved). pgbouncer pool utilization on checkout-service trending up — keeping an eye.", "ts_offset_min": -240},
        {"author_id": "scout", "text": "SEV-3 from 02:14Z resolved at 02:18Z — gateway 5xx blip from a CDN config sync. No customer-visible failure outside the window.", "ts_offset_min": -1320},
        {"author_id": "maya", "text": "thanks scout — on call switching to me at 09:00 today, drop pages here", "ts_offset_min": -480},
        {"author_id": "scout", "text": "Acknowledged. I'll keep posting hourly health checks while you're on rotation.", "ts_offset_min": -478},
    ],
    "competitive-intel": [
        {"author_id": "sam", "text": "Linear shipped 'Agents for Linear' last week. Researcher did a writeup — short version: not a threat yet, very Linear-shaped, no presence model.", "ts_offset_min": -1440},
        {"author_id": "researcher", "text": "Long version: https://acme.notion.site/linear-agents-launch — the TL;DR is that they're treating agents as features inside Linear, not as teammates with presence. Our wedge is intact.", "ts_offset_min": -1430},
        {"author_id": "sam", "text": "good. next thing to watch is Notion — they've been quiet too long.", "ts_offset_min": -1420},
        {"author_id": "researcher", "text": "Agreed — I have an alert on their changelog + careers page. Will surface anything that moves.", "ts_offset_min": -1418},
    ],
    "growth": [
        {"author_id": "sam", "text": "Activation funnel from last week: 41% → 38% drop on the first-agent-spawn step. Researcher, can you dig?", "ts_offset_min": -360},
        {"author_id": "researcher", "text": "On it. First read: looks correlated with the new env-var step we added Tuesday. Will follow up by Thu.", "ts_offset_min": -354},
        {"author_id": "sam", "text": "OKR pulse: Day-7 retention 31% (target 30%, ✅). First-agent activation 41% (target 50%, ⚠️).", "ts_offset_min": -180},
        {"author_id": "researcher", "text": "Experiment 47 (env-var defaults) finished — no statsig change to first-agent activation. We need a bigger swing here.", "ts_offset_min": -120},
    ],
}


# ---------------------------------------------------------------------------
# Ambient activity — rotating ticker in the sidebar.
# ---------------------------------------------------------------------------

AMBIENT_ACTIVITY: list[str] = [
    "Scout is monitoring #on-call · just now",
    "Researcher posted a brief in #competitive-intel · 14s ago",
    "PR Bot reviewed PR #1284 in #engineering · 1m ago",
    "Migrator is awaiting human approval in #engineering · 2m ago",
    "Researcher pulled this week's funnel data · 3m ago",
    "Scout resolved a SEV-3 in #on-call · 6m ago",
    "PR Bot landed 12 of 14 PRs reviewed yesterday · 22m ago",
    "Researcher's competitor scan ran clean overnight · 1h ago",
]


# ---------------------------------------------------------------------------
# Initial unread counts (per channel + per DM). The active channel will be
# cleared on first render.
# ---------------------------------------------------------------------------

INITIAL_UNREADS: dict[str, int] = {
    "general": 3,
    "engineering": 2,
    "on-call": 0,
    "competitive-intel": 4,
    "growth": 1,
    "dm-scout": 0,
    "dm-researcher": 1,
    "dm-maya": 2,
    "dm-sam": 0,
}
