# chat-bot

A Slack bot that maps **one thread → one Agent on Demand session**. The first
message in a thread creates a session; every subsequent message resumes it
(same Sprite, same filesystem, same agent memory).

Built on [`aod-sdk`](../../clients/python/) and
[`slack-bolt`](https://slack.dev/bolt-python/concepts). See the
[Chat Bot pattern](../../site/docs/patterns/chat-bot.md) for the full write-up.

## Install

```bash
pip install aod-sdk slack-bolt
```

Python 3.11+ required.

## Slack app setup

You need a Slack app in **socket mode** so the bot doesn't need a public URL.

1. Create an app at <https://api.slack.com/apps>.
2. **Socket Mode** → enable. Generate an app-level token with scope
   `connections:write` — this is your `SLACK_APP_TOKEN` (`xapp-...`).
3. **OAuth & Permissions** → add bot scopes: `app_mentions:read`,
   `chat:write`. Install to your workspace; copy the Bot User OAuth Token —
   this is your `SLACK_BOT_TOKEN` (`xoxb-...`).
4. **Event Subscriptions** → enable events and subscribe to `app_mention`.
5. Invite the bot to the channel(s) where you want it: `/invite @your-bot`.

## Configure

| Variable | Required | What it does |
|---|---|---|
| `SLACK_BOT_TOKEN` | yes | `xoxb-...` — bot's OAuth token |
| `SLACK_APP_TOKEN` | yes | `xapp-...` — app-level token for socket mode |
| `AOD_API_URL` | yes | Deployment URL |
| `AOD_API_TOKEN` | yes | `aod_...` |
| `AOD_AGENT_ID` | yes | Agent used for every thread |

## Run

```bash
./bot.py
# 2026-04-22 ... INFO connecting to Slack in socket mode…
```

Then in Slack:

```
@your-bot hello, what's in /workspace?
# Bot replies in-thread. Continue the conversation with follow-up mentions
# in the same thread — those resume the same session.
```

## What to look at

- `bot.py:on_mention` — the whole lifecycle: first message creates a session
  via `client.sessions.create(...)`, subsequent messages resume via
  `client.sessions.prompt(...)`.
- `bot.py:_drain_stdout` — `client.sessions.stream(session_id)` is a context
  manager yielding typed `StreamEvent`s. The bot collects stdout until a
  terminal event and posts the result back to the thread.
- `ConflictError` handling — if a user fires off a second message before the
  first one finishes, `prompt()` raises `ConflictError` (HTTP 409). The bot
  posts a short "still working" reply; a production implementation would
  enqueue messages per thread.

## Production notes

- **Storage.** `thread_sessions` is an in-memory dict; it disappears on
  restart. Persist to Redis, Postgres, or SQLite so threads survive deploys.
- **Long replies.** Slack's `chat.postMessage` caps a block at ~3000
  characters. For longer agent output, chunk the reply or post a snippet.
- **Session lifetime.** Sprites aren't free. Call
  `client.sessions.terminate(id)` when a thread goes idle (cron the bot or
  track last-activity per thread).
- **Streaming to Slack.** Slack doesn't support true streaming; this bot
  waits for the terminal event and posts once. For a "typing…" UX, use
  [`chat.update`](https://api.slack.com/methods/chat.update) to edit the
  message as new `output` events arrive.
