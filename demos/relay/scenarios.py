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
