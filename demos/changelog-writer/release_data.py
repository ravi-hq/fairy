"""
release_data.py — Pre-loaded sample releases with git logs and all three output formats.
"""

RELEASES = {
    "v2.4.0-cli": {
        "id": "v2.4.0-cli",
        "label": "v2.4.0 — Developer CLI",
        "git_log": """\
a3f92c1 feat: add --watch flag to auto-rerun on file changes
b12e445 feat: support .env file loading from project root
c98d230 feat: parallel test execution with --jobs flag
d44f112 fix: crash when config file has trailing comma
e5a9901 fix: incorrect exit code when tests partially fail
f88bc23 fix: --output flag ignored when using --quiet
g12a445 perf: 3x faster startup by lazy-loading plugins
h99f031 perf: reduce memory usage by 40% on large projects
i44d820 chore: drop support for Node.js 16 (EOL)
j77e112 docs: add examples for all new flags""",
        "changelog": [
            "## [2.4.0] - 2026-04-30",
            "",
            "### Added",
            "- `--watch` flag: automatically reruns on file changes",
            "- `.env` file loading from project root (no config required)",
            "- Parallel test execution via `--jobs <n>` flag",
            "",
            "### Fixed",
            "- Crash when config file contains trailing comma",
            "- Incorrect exit code on partial test failure",
            "- `--output` flag now respected when using `--quiet`",
            "",
            "### Performance",
            "- 3× faster startup via lazy plugin loading",
            "- 40% memory reduction on large projects",
            "",
            "### Breaking Changes",
            "- Node.js 16 support removed (reached EOL)",
        ],
        "blogpost": [
            "We're shipping v2.4.0 today and it's a good one.",
            "",
            "The headline feature is --watch mode. If you've ever wished the tool just",
            "stayed running and picked up your changes automatically — that's what",
            "--watch does. Combine it with the new --jobs flag for parallel execution",
            "and you've got a significantly faster feedback loop.",
            "",
            "We also quietly landed .env file loading. Drop a .env in your project",
            "root and the tool picks it up automatically. No extra config, no wrapper",
            "scripts.",
            "",
            "Under the hood, startup is 3× faster thanks to lazy plugin loading, and",
            "memory usage is down 40% on large projects. Both of those were",
            "long-standing complaints and we're glad to finally have them addressed.",
            "",
            "One thing to be aware of: Node.js 16 support is gone. It hit EOL in",
            "September and carrying it was holding back some of the performance work.",
            "If you're still on it, upgrading to Node 18 or 20 should be painless.",
        ],
        "tweetthread": [
            "🚀 v2.4.0 is out — here's what's new: 🧵",
            "",
            "1/ --watch mode is finally here. Run once, stay running, auto-rerun on",
            "changes. The workflow you've been asking for.",
            "",
            "2/ Parallel test execution lands with --jobs. Large test suites are now",
            "dramatically faster. Early testers saw 4-8x speedups.",
            "",
            "3/ .env loading works out of the box now. Drop a .env in your project",
            "root. That's it. No config required.",
            "",
            "4/ Startup is 3× faster. Memory is down 40%. These aren't rounding",
            "errors — they're the result of lazy plugin loading we've been working",
            "on for months.",
            "",
            "5/ We fixed the config trailing-comma crash, the exit code bug, and the",
            "--quiet/--output conflict. Thank you to everyone who reported these.",
            "",
            "6/ One breaking change: Node 16 is gone. It reached EOL in September.",
            "If you're still on it, now's a good time to upgrade.",
            "",
            "Full changelog: github.com/example/tool/releases/tag/v2.4.0",
        ],
    },

    "v1.8.0-api": {
        "id": "v1.8.0-api",
        "label": "v1.8.0 — SaaS API",
        "git_log": """\
k23f891 feat: webhooks now support retry with exponential backoff
l45e990 feat: add /v2/batch endpoint for up to 100 requests per call
m88d112 feat: API keys now support IP allowlist restrictions
n12c445 feat: streaming responses now include token usage in final chunk
o99b230 fix: race condition in webhook delivery causing duplicate events
p44a112 fix: /v2/models endpoint returning stale cache for 10 minutes
q77f031 fix: pagination cursor invalid after 24 hours
r23e445 perf: webhook fanout latency reduced from 800ms to 90ms avg
s88d990 security: rotate signing keys for all webhook payloads (no action required)
t45c112 breaking: deprecated /v1/complete endpoint removed (migrated to /v2/generate)""",
        "changelog": [
            "## [1.8.0] - 2026-04-30",
            "",
            "### Added",
            "- Webhook retries with exponential backoff (configurable max attempts)",
            "- `/v2/batch` endpoint: up to 100 requests in a single API call",
            "- IP allowlist restrictions on API keys",
            "- Token usage included in final chunk of streaming responses",
            "",
            "### Fixed",
            "- Race condition in webhook delivery that caused duplicate events",
            "- `/v2/models` returning stale data for up to 10 minutes",
            "- Pagination cursor expiring after 24 hours",
            "",
            "### Performance",
            "- Webhook fanout latency: 800ms → 90ms average",
            "",
            "### Security",
            "- Signing keys rotated for all webhook payloads (no action required)",
            "",
            "### Breaking Changes",
            "- `/v1/complete` endpoint removed — use `/v2/generate`",
        ],
        "blogpost": [
            "v1.8.0 is live. A few things worth calling out.",
            "",
            "Webhooks got a significant reliability upgrade. We've added exponential",
            "backoff retries, so transient failures on your end no longer mean missed",
            "events. We also fixed a race condition that was occasionally causing",
            "duplicate deliveries — if you built deduplication logic as a workaround,",
            "you can probably remove it.",
            "",
            "The new /v2/batch endpoint lets you pack up to 100 requests into a",
            "single call. If you're doing high-volume processing, this should",
            "meaningfully reduce both latency and the number of connections you need",
            "to manage.",
            "",
            "On the performance side: webhook fanout latency dropped from 800ms to",
            "90ms on average. That's a 9× improvement, and it shows up immediately",
            "for anyone consuming real-time events.",
            "",
            "One hard breaking change in this release: /v1/complete is gone. We",
            "deprecated it eight months ago and the migration path to /v2/generate",
            "is straightforward. Check the migration guide if you haven't already.",
        ],
        "tweetthread": [
            "📡 API v1.8.0 is out. Reliability, batch processing, and a long-overdue",
            "perf win. Here's the rundown: 🧵",
            "",
            "1/ Webhooks now retry with exponential backoff. Transient failures on",
            "your end won't cause missed events anymore. Configurable max attempts.",
            "",
            "2/ We also fixed the duplicate-delivery race condition. If you built",
            "dedup logic as a workaround, you can probably rip it out.",
            "",
            "3/ New: /v2/batch. Pack up to 100 requests into one API call.",
            "Big win for high-volume processing pipelines.",
            "",
            "4/ Webhook fanout latency: 800ms → 90ms. That's not a typo.",
            "9× faster, and it's live for everyone now.",
            "",
            "5/ Streaming responses now include token usage in the final chunk.",
            "Easier cost tracking without a separate billing API call.",
            "",
            "6/ Breaking: /v1/complete is removed. Migrate to /v2/generate.",
            "Been deprecated for 8 months — migration guide in the docs.",
            "",
            "Full changelog: api.example.com/changelog#v1.8.0",
        ],
    },

    "v3.1.0-mobile": {
        "id": "v3.1.0-mobile",
        "label": "v3.1.0 — Mobile App",
        "git_log": """\
u12b891 feat: offline mode — read and compose while disconnected
v45a990 feat: biometric authentication support (Face ID / fingerprint)
w88f112 feat: bulk actions — archive or delete up to 50 items at once
x23e445 feat: share to other apps via native share sheet
y99d230 fix: crash on launch when notification permission denied
z44c112 fix: dark mode inconsistencies in settings screen
aa77b031 fix: push notifications not delivered when app in background (iOS 17)
bb12a445 perf: 60% faster initial load time
cc45f990 perf: smoother scrolling on older devices
dd88e112 accessibility: full VoiceOver support for all new features""",
        "changelog": [
            "## [3.1.0] - 2026-04-30",
            "",
            "### Added",
            "- Offline mode: read and compose content without a connection",
            "- Biometric authentication (Face ID and fingerprint)",
            "- Bulk actions: archive or delete up to 50 items at once",
            "- Native share sheet integration",
            "",
            "### Fixed",
            "- Crash on launch when notification permission is denied",
            "- Dark mode visual inconsistencies in settings screen",
            "- Push notifications not delivered while app is backgrounded (iOS 17)",
            "",
            "### Performance",
            "- Initial load time reduced by 60%",
            "- Scrolling smoothness improved on older devices",
            "",
            "### Accessibility",
            "- Full VoiceOver support added for all features in this release",
        ],
        "blogpost": [
            "Version 3.1.0 is available on the App Store and Google Play.",
            "",
            "The biggest addition is offline mode. You can now read everything in",
            "your inbox and compose new content without an internet connection.",
            "Changes sync automatically when you reconnect. It's something we've",
            "been building toward for a while, and we're happy with how it landed.",
            "",
            "We also added biometric auth — Face ID on iPhone, fingerprint on",
            "Android. Quick to set up in Settings > Security, and it works",
            "alongside your existing passcode.",
            "",
            "For heavy users: bulk actions are here. Select up to 50 items and",
            "archive or delete in one tap. A lot of you asked for this.",
            "",
            "On performance: initial load is 60% faster. If you've been frustrated",
            "by the startup time, especially on older devices, you should notice",
            "a meaningful difference.",
            "",
            "We also fixed the iOS 17 background notification bug that has been",
            "affecting a portion of iPhone users since the OS update. Sorry it",
            "took this long — it was a tricky interaction with the new notification",
            "delivery system.",
        ],
        "tweetthread": [
            "📱 v3.1.0 is live on App Store + Google Play. Big one. 🧵",
            "",
            "1/ Offline mode is here. Read your full inbox, compose messages,",
            "all without a connection. Syncs when you're back online.",
            "",
            "2/ Biometric auth: Face ID and fingerprint unlock are now supported.",
            "Turn it on in Settings > Security. Works alongside your passcode.",
            "",
            "3/ Bulk actions landed. Select up to 50 items, archive or delete",
            "in one tap. Many of you asked for this. Here it is.",
            "",
            "4/ Initial load is 60% faster. Scrolling is smoother on older",
            "devices too. The app just feels better to use.",
            "",
            "5/ Fixed: the iOS 17 background notification bug. If push notifications",
            "stopped working after you updated to iOS 17, this fixes it.",
            "",
            "6/ Full VoiceOver support for everything new in this release.",
            "Accessibility isn't an afterthought — it ships with the feature.",
            "",
            "Update available now. Tap the banner or visit the App Store.",
        ],
    },
}
