"""
Data Detective — sample datasets, analysis scripts, and pre-scripted follow-up responses.
"""

# ---------------------------------------------------------------------------
# Dataset 1: E-commerce Sales
# ---------------------------------------------------------------------------

ECOMMERCE_ROWS = [
    {"date": "2024-03-01", "product": "Laptop Pro",       "category": "Electronics",  "revenue": 4200,  "units_sold": 3,   "region": "East"},
    {"date": "2024-03-01", "product": "Coffee Maker",     "category": "Appliances",   "revenue": 890,   "units_sold": 7,   "region": "West"},
    {"date": "2024-03-02", "product": "Wireless Mouse",   "category": "Electronics",  "revenue": 640,   "units_sold": 8,   "region": "East"},
    {"date": "2024-03-02", "product": "Blender Pro",      "category": "Appliances",   "revenue": 1200,  "units_sold": 10,  "region": "West"},
    {"date": "2024-03-03", "product": "Laptop Pro",       "category": "Electronics",  "revenue": 2800,  "units_sold": 2,   "region": "North"},
    {"date": "2024-03-03", "product": "Air Fryer",        "category": "Appliances",   "revenue": 1560,  "units_sold": 12,  "region": "West"},
    {"date": "2024-03-04", "product": "USB-C Hub",        "category": "Electronics",  "revenue": 950,   "units_sold": 19,  "region": "East"},
    {"date": "2024-03-04", "product": "Stand Mixer",      "category": "Appliances",   "revenue": 2100,  "units_sold": 6,   "region": "South"},
    {"date": "2024-03-05", "product": "Monitor 27in",     "category": "Electronics",  "revenue": 3600,  "units_sold": 4,   "region": "East"},
    {"date": "2024-03-05", "product": "Coffee Maker",     "category": "Appliances",   "revenue": 760,   "units_sold": 6,   "region": "West"},
    {"date": "2024-03-06", "product": "Laptop Pro",       "category": "Electronics",  "revenue": 1400,  "units_sold": 1,   "region": "North"},
    {"date": "2024-03-06", "product": "Blender Pro",      "category": "Appliances",   "revenue": 960,   "units_sold": 8,   "region": "West"},
    {"date": "2024-03-07", "product": "Wireless Mouse",   "category": "Electronics",  "revenue": 480,   "units_sold": 6,   "region": "South"},
    {"date": "2024-03-07", "product": "Air Fryer",        "category": "Appliances",   "revenue": 1690,  "units_sold": 13,  "region": "East"},
    {"date": "2024-03-08", "product": "USB-C Hub",        "category": "Electronics",  "revenue": 700,   "units_sold": 14,  "region": "West"},
    {"date": "2024-03-08", "product": "Stand Mixer",      "category": "Appliances",   "revenue": 1750,  "units_sold": 5,   "region": "South"},
    {"date": "2024-03-09", "product": "Monitor 27in",     "category": "Electronics",  "revenue": 2700,  "units_sold": 3,   "region": "North"},
    {"date": "2024-03-09", "product": "Coffee Maker",     "category": "Appliances",   "revenue": 1020,  "units_sold": 8,   "region": "East"},
    {"date": "2024-03-10", "product": "Laptop Pro",       "category": "Electronics",  "revenue": 2100,  "units_sold": 1,   "region": "East"},
    {"date": "2024-03-10", "product": "Blender Pro",      "category": "Appliances",   "revenue": 840,   "units_sold": 7,   "region": "West"},
    {"date": "2024-03-11", "product": "Wireless Mouse",   "category": "Electronics",  "revenue": 560,   "units_sold": 7,   "region": "South"},
    {"date": "2024-03-11", "product": "Air Fryer",        "category": "Appliances",   "revenue": 1430,  "units_sold": 11,  "region": "North"},
    # THE SPIKE — Tuesday 2024-03-12: revenue ~8x normal
    {"date": "2024-03-12", "product": "Wireless Mouse",   "category": "Electronics",  "revenue": 15680, "units_sold": 847, "region": "East"},
    {"date": "2024-03-13", "product": "USB-C Hub",        "category": "Electronics",  "revenue": 550,   "units_sold": 11,  "region": "West"},
    {"date": "2024-03-13", "product": "Stand Mixer",      "category": "Appliances",   "revenue": 2450,  "units_sold": 7,   "region": "East"},
    {"date": "2024-03-14", "product": "Monitor 27in",     "category": "Electronics",  "revenue": 1800,  "units_sold": 2,   "region": "North"},
    {"date": "2024-03-14", "product": "Coffee Maker",     "category": "Appliances",   "revenue": 640,   "units_sold": 5,   "region": "West"},
    {"date": "2024-03-15", "product": "Laptop Pro",       "category": "Electronics",  "revenue": 1200,  "units_sold": 1,   "region": "South"},  # declining
    {"date": "2024-03-15", "product": "Air Fryer",        "category": "Appliances",   "revenue": 1170,  "units_sold": 9,   "region": "West"},
    {"date": "2024-03-16", "product": "Wireless Mouse",   "category": "Electronics",  "revenue": 400,   "units_sold": 5,   "region": "East"},
]

ECOMMERCE_COLUMNS = ["date", "product", "category", "revenue", "units_sold", "region"]

# ---------------------------------------------------------------------------
# Dataset 2: User Signup Funnel
# ---------------------------------------------------------------------------

FUNNEL_ROWS = [
    {"step": 1, "step_name": "Landing Page",        "users": 10000, "conversion_rate": "100%",  "drop_off_pct": "0%"},
    {"step": 2, "step_name": "Sign Up Form",         "users": 7800,  "conversion_rate": "78%",   "drop_off_pct": "22%"},
    {"step": 3, "step_name": "Email Verification",   "users": 6900,  "conversion_rate": "88.5%", "drop_off_pct": "11.5%"},
    {"step": 4, "step_name": "Profile Setup",        "users": 6200,  "conversion_rate": "89.9%", "drop_off_pct": "10.1%"},
    {"step": 5, "step_name": "Add Payment Method",   "users": 2356,  "conversion_rate": "38%",   "drop_off_pct": "62%"},  # ANOMALY
    {"step": 6, "step_name": "First Purchase",       "users": 2100,  "conversion_rate": "89.1%", "drop_off_pct": "10.9%"},
    {"step": 7, "step_name": "Return Visit",         "users": 1680,  "conversion_rate": "80%",   "drop_off_pct": "20%"},
]

FUNNEL_COLUMNS = ["step", "step_name", "users", "conversion_rate", "drop_off_pct"]

# ---------------------------------------------------------------------------
# Dataset 3: API Response Times
# ---------------------------------------------------------------------------

API_ROWS = [
    {"endpoint": "/api/search",         "p50_ms": 340,  "p95_ms": 2100, "p99_ms": 8400, "requests_per_day": 142000, "error_rate": "0.3%"},  # TAIL LATENCY
    {"endpoint": "/api/products",       "p50_ms": 45,   "p95_ms": 120,  "p99_ms": 210,  "requests_per_day": 89000,  "error_rate": "0.1%"},
    {"endpoint": "/api/cart",           "p50_ms": 38,   "p95_ms": 95,   "p99_ms": 180,  "requests_per_day": 67000,  "error_rate": "0.2%"},
    {"endpoint": "/api/checkout",       "p50_ms": 120,  "p95_ms": 340,  "p99_ms": 480,  "requests_per_day": 12000,  "error_rate": "0.4%"},
    {"endpoint": "/api/user/profile",   "p50_ms": 28,   "p95_ms": 75,   "p99_ms": 140,  "requests_per_day": 55000,  "error_rate": "0.1%"},
    {"endpoint": "/api/recommendations","p50_ms": 95,   "p95_ms": 280,  "p99_ms": 420,  "requests_per_day": 78000,  "error_rate": "0.2%"},
    {"endpoint": "/api/export",         "p50_ms": 890,  "p95_ms": 3200, "p99_ms": 4900, "requests_per_day": 3400,   "error_rate": "4.2%"},  # HIGH ERROR RATE
    {"endpoint": "/api/auth/login",     "p50_ms": 55,   "p95_ms": 140,  "p99_ms": 260,  "requests_per_day": 44000,  "error_rate": "0.3%"},
    {"endpoint": "/api/auth/refresh",   "p50_ms": 22,   "p95_ms": 60,   "p99_ms": 110,  "requests_per_day": 38000,  "error_rate": "0.1%"},
    {"endpoint": "/api/orders",         "p50_ms": 68,   "p95_ms": 190,  "p99_ms": 310,  "requests_per_day": 29000,  "error_rate": "0.2%"},
    {"endpoint": "/api/inventory",      "p50_ms": 52,   "p95_ms": 155,  "p99_ms": 290,  "requests_per_day": 18000,  "error_rate": "0.1%"},
    {"endpoint": "/api/reviews",        "p50_ms": 41,   "p95_ms": 108,  "p99_ms": 195,  "requests_per_day": 24000,  "error_rate": "0.2%"},
    {"endpoint": "/api/wishlist",       "p50_ms": 33,   "p95_ms": 88,   "p99_ms": 160,  "requests_per_day": 15000,  "error_rate": "0.1%"},
    {"endpoint": "/api/notifications",  "p50_ms": 19,   "p95_ms": 55,   "p99_ms": 98,   "requests_per_day": 61000,  "error_rate": "0.1%"},
    {"endpoint": "/api/analytics",      "p50_ms": 210,  "p95_ms": 580,  "p99_ms": 490,  "requests_per_day": 8200,   "error_rate": "0.3%"},
    {"endpoint": "/api/upload",         "p50_ms": 450,  "p95_ms": 1200, "p99_ms": 2100, "requests_per_day": 6700,   "error_rate": "0.4%"},
    {"endpoint": "/api/webhooks",       "p50_ms": 75,   "p95_ms": 200,  "p99_ms": 340,  "requests_per_day": 4100,   "error_rate": "0.2%"},
    {"endpoint": "/api/admin/reports",  "p50_ms": 340,  "p95_ms": 890,  "p99_ms": 1400, "requests_per_day": 0,      "error_rate": "0.0%"},  # DEAD CODE
    {"endpoint": "/api/legacy/import",  "p50_ms": 120,  "p95_ms": 310,  "p99_ms": 540,  "requests_per_day": 0,      "error_rate": "0.0%"},  # DEAD CODE
    {"endpoint": "/api/coupons",        "p50_ms": 48,   "p95_ms": 130,  "p99_ms": 240,  "requests_per_day": 11000,  "error_rate": "0.2%"},
]

API_COLUMNS = ["endpoint", "p50_ms", "p95_ms", "p99_ms", "requests_per_day", "error_rate"]

# ---------------------------------------------------------------------------
# Dataset metadata
# ---------------------------------------------------------------------------

DATASETS = {
    "ecommerce": {
        "id": "ecommerce",
        "title": "E-commerce Sales",
        "description": "30 days of product sales across regions and categories",
        "rows": ECOMMERCE_ROWS,
        "columns": ECOMMERCE_COLUMNS,
        "row_count": len(ECOMMERCE_ROWS),
        "icon": "🛒",
    },
    "funnel": {
        "id": "funnel",
        "title": "User Signup Funnel",
        "description": "Step-by-step conversion rates through the onboarding flow",
        "rows": FUNNEL_ROWS,
        "columns": FUNNEL_COLUMNS,
        "row_count": len(FUNNEL_ROWS),
        "icon": "📊",
    },
    "api": {
        "id": "api",
        "title": "API Response Times",
        "description": "Latency and error rates across 20 API endpoints",
        "rows": API_ROWS,
        "columns": API_COLUMNS,
        "row_count": len(API_ROWS),
        "icon": "⚡",
    },
}

# ---------------------------------------------------------------------------
# Initial analysis — narrative prose, one sentence/paragraph per chunk
# ---------------------------------------------------------------------------

INITIAL_FINDINGS = {
    "ecommerce": [
        "Alright, let me walk through what I'm seeing in this sales dataset.",
        "The date range covers March 1–16, 2024 — 30 transactions across four regions (East, West, North, South) and two main categories: Electronics and Appliances.",
        "First thing that jumps out: revenue totals. Most days run between $1,400 and $5,200. Normal enough.",
        "Then we hit Tuesday, March 12th. Total revenue that day: $15,680 — roughly 8x the daily average of $1,940.",
        "What's interesting is it's not a high-value item driving that spike. It's the Wireless Mouse, a $18.50/unit product, logging 847 units sold. Every other day this product moves 5–8 units.",
        "That revenue-per-unit ratio ($18.50 vs the product's normal $28.40) actually suggests a bulk discount or flash sale — not a data entry error. Someone bought 847 mice at a steep markdown.",
        "Shifting to categories: Appliances holds steady throughout the window. Electronics, on the other hand, shows a clear declining revenue trend — Laptop Pro drops from $4,200 on March 1 down to $1,200 by March 15.",
        "Now let's talk regions. East averages $4,920/day. West averages $2,840/day — a 42% gap that's consistent, not a fluke.",
        "The product mix tells the story: West's sales are 67% Appliances. Electronics — your highest-margin category — represents only 12% of West's transactions. East is nearly inverse at 38% Appliances.",
        "This doesn't look like weak demand in the West. It looks like a distribution or marketing coverage gap. Electronics aren't being pushed there the way they are in East.",
        "Summary: three things to investigate — the March 12 bulk order event, the Electronics category slide, and the West region's product-mix imbalance.",
    ],
    "funnel": [
        "Let me read through this funnel data step by step.",
        "You start with 10,000 visitors on the Landing Page. The first real ask — Sign Up Form — drops you to 7,800. A 22% drop here is actually pretty typical for a form gate.",
        "Email Verification: 6,900 users, 11.5% drop. Profile Setup: 6,200, 10.1% drop. Both healthy — users who've verified their email are committed.",
        "Then we reach Step 5: Add Payment Method.",
        "3,844 users just... left. You go from 6,200 to 2,356 in a single step. That's a 62% drop-off rate.",
        "To put that in context: every other step in this funnel sits between 10% and 22% drop-off. Step 5 is 3 to 4 times worse than any of them.",
        "After that cliff, the funnel actually recovers beautifully — First Purchase converts at 89.1%, Return Visit at 80%. The users who make it past payment are high-intent and sticky.",
        "So the bottleneck is entirely the payment step. The product works. The retention works. You're just hemorrhaging users at the moment you ask for a credit card.",
        "There's a secondary signal worth flagging: mobile users converting at roughly half the rate of desktop at this step is a known pattern. Payment forms on mobile — too many fields, keyboard switching between number/text, no autofill — are notoriously painful.",
        "The math on fixing this is stark: if you can move Step 5's drop-off from 62% to even 40%, you'd recover roughly 1,400 additional paying customers per 10,000 visitors.",
        "The diagnosis: payment form friction is the single biggest lever in this entire funnel. Everything upstream and downstream is working.",
    ],
    "api": [
        "Let me scan through this API performance data — 20 endpoints, latency at p50/p95/p99, plus error rates.",
        "Starting with the good news: most of your API surface is clean. Median latencies (p50) are sub-100ms across the board. p99 values are generally under 500ms. That's solid.",
        "Now the anomalies — and there are two clear ones.",
        "First: /api/search. p50 is 340ms — a bit sluggish but livable. p95 is 2,100ms. p99 is 8,400ms.",
        "That p99 means 1 in every 100 search requests takes over 8 seconds. For a search endpoint serving 142,000 requests per day, that's roughly 1,420 users a day hitting a near-timeout experience.",
        "The gap between p50 (340ms) and p99 (8,400ms) is the tell: this isn't a slow endpoint overall, it's a tail latency problem. Median is fine. Something in the search code path only triggers on certain inputs — a missing index on a particular query pattern, or a data distribution edge case that forces a full table scan.",
        "Second anomaly: /api/export. Error rate of 4.2% — everything else in this dataset is under 0.5%. And it's slow: p50 of 890ms, p99 of 4,900ms.",
        "Export jobs that time out or hit resource limits are the usual culprit. Someone's exporting large datasets, the job runs long, and a chunk of them fail before completion.",
        "Third finding — and this one's subtle: two endpoints show exactly zero requests per day. /api/admin/reports and /api/legacy/import.",
        "Zero traffic endpoints still in the codebase are either internal tooling not in your public traffic logs, deprecated routes someone forgot to remove, or monitoring blind spots. Either way, dead code with an active API surface is a security liability — especially if those endpoints haven't been patched alongside the rest of the service.",
        "Priorities: fix the /api/search tail latency first (it affects the most users), investigate /api/export timeouts second, and audit those two dead endpoints for safe removal.",
    ],
}

# ---------------------------------------------------------------------------
# Follow-up Q&A — pre-scripted responses keyed by (dataset_id, keyword)
# ---------------------------------------------------------------------------

FOLLOWUP_RESPONSES = {
    "ecommerce": [
        {
            "keywords": ["spike", "march 12", "march 12th", "12", "caused"],
            "lines": [
                "Good question — let's dig into that March 12th spike.",
                "Looking at the units_sold column for that date: 847 units of the Wireless Mouse vs a daily average of 94 across all products. That's a 9x unit surge, not just a revenue one.",
                "The revenue-per-unit ratio on March 12th is $18.50. The Wireless Mouse's normal per-unit revenue is $28.40. That 35% markdown is the key signal.",
                "A data entry error would typically show inflated revenue with normal or unchanged units. Here the units are astronomically high and the per-unit price is lower. That pattern almost always means a bulk discount or a flash sale — someone placed a large B2B order or you ran a limited-time promotion.",
                "As we saw in the initial analysis, this is almost certainly a real transaction event rather than bad data. Do you have a promotions calendar or CRM records for March 12th? If a bulk order came through a sales rep, it might not be in your standard promo tracking.",
            ],
        },
        {
            "keywords": ["west", "region", "underperform", "gap"],
            "lines": [
                "Let's revisit the West region numbers we flagged.",
                "West region averages $2,840 revenue per day. East averages $4,920 per day — a 42% gap, and it's consistent across every day in the dataset, not driven by one outlier.",
                "The product mix is where the story lives. As we noted in the initial analysis, West's sales are 67% Appliances. Electronics — your highest-margin category — makes up only 12% of West transactions. East is nearly the inverse.",
                "This rules out 'low demand' as the cause. Appliance demand in West is healthy. The issue is that Electronics aren't reaching the West the same way they reach East — this points to distribution coverage, retail partnerships, or marketing spend allocation.",
                "One actionable next step: look at whether your Electronics advertising budget is geo-targeted and whether West has fewer retail or distribution partners carrying your Electronics line. A targeted campaign or a new channel partner in the West could close a meaningful portion of that $2,080/day gap.",
            ],
        },
    ],
    "funnel": [
        {
            "keywords": ["payment", "drop", "drop-off", "62", "step 5", "why"],
            "lines": [
                "The 62% drop at payment is the defining problem in this funnel — let's break down what's usually behind it.",
                "As we saw in the initial analysis, every other step in the funnel sits between 10–22% drop-off. Step 5 is 3 to 4 times worse, which means it's not a normal 'intent filter' — something is actively driving people away.",
                "The most common causes in this pattern: friction in the payment form itself (too many required fields, no saved card options, forced account creation), missing trust signals at that exact step (no lock icon, no 'cancel anytime' reassurance), and mobile UX issues.",
                "A targeted fix: A/B test a one-click payment option like Stripe Link or Apple Pay at Step 5. These remove the manual card entry entirely for users who've used them before. In most implementations, one-click options improve payment step conversion by 20–35%.",
                "Given the math we ran — recovering 1,400 users per 10,000 visitors if you drop the rate to 40% — even a partial fix here has a higher ROI than any other optimization in this funnel.",
            ],
        },
        {
            "keywords": ["mobile", "desktop", "device", "half"],
            "lines": [
                "The mobile vs desktop split is worth quantifying carefully.",
                "If mobile converts at half the desktop rate at Step 5, and mobile represents 60%+ of your traffic (the industry average for consumer apps), then the rough math is significant.",
                "Let's say desktop converts 55% at Step 5 and mobile converts 27%. With 60% mobile traffic: (0.4 * 0.55) + (0.6 * 0.27) = 0.22 + 0.162 = 38.2% blended — which lines up exactly with the 38% we see in the data.",
                "That means the mobile experience alone is probably responsible for most of the gap we flagged in the initial analysis. If you could bring mobile up to desktop parity, Step 5 conversion would jump from 38% to roughly 55%, recovering around 1,100 additional users per 10,000.",
                "The fastest diagnostic: split your funnel analytics by device type at each step. If the mobile/desktop divergence is concentrated at Step 5 (payment) and not earlier steps, the fix is payment UX — not your broader mobile experience.",
            ],
        },
    ],
    "api": [
        {
            "keywords": ["search", "/api/search", "8400", "8,400", "tail", "latency", "slow"],
            "lines": [
                "The /api/search tail latency is the most user-facing issue in this dataset — let's dig in.",
                "As we flagged in the initial analysis, p99 of 8,400ms means 1 in 100 search requests hits an 8-second wall. With 142,000 requests per day, that's roughly 1,420 slow experiences daily.",
                "The p50-to-p99 gap is the diagnostic key. p50 is 340ms — totally acceptable. p99 is 8,400ms. That 25x spread tells you this isn't a uniformly slow endpoint. It's a code path that only triggers under specific conditions.",
                "The usual suspects for this pattern: an unindexed query that gets triggered by certain search terms or filter combinations, a query that's efficient on small result sets but degrades badly on large ones (missing LIMIT or pagination in a subquery), or a lock contention issue that only shows up under concurrent load.",
                "Concrete next step: pull your slow query log filtered to the search handler and look for queries taking over 2,000ms. Sort by frequency — you're looking for a query that appears in 1–5% of search requests and takes 5–10x longer than the median query. That's your culprit.",
            ],
        },
        {
            "keywords": ["dead", "endpoint", "zero", "0 requests", "legacy", "admin", "unused"],
            "lines": [
                "The two zero-traffic endpoints are worth a closer look — let's think through what they might be.",
                "As we noted in the initial analysis, /api/admin/reports and /api/legacy/import both show exactly 0 requests per day. That's an unusual reading — most dormant endpoints get at least occasional traffic from monitoring pings or stale clients.",
                "Three likely scenarios: they're internal-only endpoints excluded from your public traffic logging (common for admin routes), they're deprecated routes that were soft-retired but never removed from the router, or they represent a monitoring gap — traffic is happening but not being captured.",
                "The security angle is real. Dead endpoints that haven't been actively maintained may have lagged behind your security patching cycle. If /api/legacy/import handles file uploads or data ingestion, an unpatched vulnerability there is a meaningful risk even if traffic is low.",
                "Recommended action: check your router/controller config to confirm both endpoints still exist and resolve correctly. If they return 404, they can be removed cleanly. If they return 200, audit the last time they were updated and whether they share authentication middleware with your active endpoints.",
            ],
        },
        {
            "keywords": ["export", "/api/export", "error", "4.2", "4%"],
            "lines": [
                "The /api/export error rate stands out sharply from the rest of the dataset.",
                "As we identified in the initial analysis, /api/export has a 4.2% error rate — everything else in this API surface is under 0.5%. Combined with a p50 of 890ms and p99 of 4,900ms, this endpoint is both slow and unreliable.",
                "Export endpoints fail in predictable ways: the job takes longer than the HTTP timeout window (typically 30–60 seconds for large datasets), memory pressure from loading too much data at once, or resource limits that only trigger on large exports.",
                "The error rate of 4.2% on 3,400 daily requests means roughly 143 export failures per day. For users, a failed export is high-friction — they've waited minutes for a result and get nothing.",
                "The fix pattern here is well-established: move the export to an async job. The request initiates the export, returns a job ID immediately, and the client polls or gets notified when the file is ready. This sidesteps the timeout issue entirely and gives you better observability into failure modes.",
            ],
        },
    ],
}

GENERIC_FOLLOWUP = {
    "ecommerce": [
        "That's an interesting angle on the sales data.",
        "Based on what we found in the initial analysis — the March 12th spike, the Electronics decline, and the West region gap — the pattern you're describing connects most directly to the product mix and regional distribution story.",
        "Without additional data (promotions records, regional marketing spend, returns/refund data), it's hard to be more precise, but those three findings are the strongest leads in this dataset.",
        "What specific slice of the data would be most useful to dig into further?",
    ],
    "funnel": [
        "Good follow-up question on the funnel.",
        "Anchoring back to what we found: the 62% payment drop-off is the dominant story here, but the upstream steps (Sign Up Form at 22%, Return Visit at 20%) are also worth watching as the funnel scales.",
        "The funnel post-payment is actually healthy — 89% first purchase rate and 80% return visit suggest product-market fit is solid. The investment should go into reducing friction before users ever reach the payment wall.",
        "Is there a specific step or segment you'd like to think through?",
    ],
    "api": [
        "Good question — let me connect that back to what we found in the data.",
        "The three signals we identified were: /api/search tail latency (p99: 8,400ms), /api/export error rate (4.2%), and two zero-traffic endpoints that warrant a security audit.",
        "The search latency affects the most users by volume (142,000 requests/day). The export errors affect reliability for a smaller but likely high-value segment. The dead endpoints are a background risk.",
        "Which of those threads would you like to pull on?",
    ],
}


def get_followup_response(dataset_id: str, question: str) -> list[str]:
    """Match a user question to a pre-scripted response, or return a generic fallback."""
    question_lower = question.lower()
    responses = FOLLOWUP_RESPONSES.get(dataset_id, [])
    for entry in responses:
        if any(kw in question_lower for kw in entry["keywords"]):
            return entry["lines"]
    return GENERIC_FOLLOWUP.get(dataset_id, ["I don't have a specific answer for that based on the current dataset."])
