# Patterns

Categories of products you can build when AI agents are a primitive in your
stack. Each page below is a working pattern, not a copy-paste recipe — the
shape of the integration, the gotchas, and the SDK calls that hold it
together.

If you haven't read [Why Agent on Demand](../api/why.md) yet, that's the
context for *why* these patterns are interesting. This page is about what
to do once you're convinced.

Python examples use the official [`aod-sdk`](../sdks/python.md) package
(`pip install aod-sdk`). TypeScript equivalents use
[`@ravi-hq/aod-sdk`](../sdks/typescript.md).

## Triggered by user actions in your product

Your app already has events — a card moves, a ticket arrives, a row is
created. Each of those can spawn an agent in a fresh sandbox and stream
its work back into the UI.

- **[Chat Bot](chat-bot.md)** — one chat thread → one session. Slack,
  Discord, an internal tool. Multi-turn for free, because the Sprite
  filesystem is still warm on the next message.
- **[Internal Dashboard](dashboard.md)** — a web UI where users kick off
  agent runs without holding their own API tokens. Your backend is the
  trust boundary; AOD is the runtime.

The kanban-that-ships-PRs example from the landing page is a special case
of [Internal Dashboard](dashboard.md): the trigger is a card move, the
work is "open the PR," and the SSE stream drives the card transition.

## Triggered by code, CI, or scheduled jobs

The visitor isn't a human — the trigger is a webhook, a cron, or a CI run.

- **[CI Bot](ci-bot.md)** — GitHub Actions, GitLab pipelines, Buildkite.
  Spawn an agent on every PR or every merge to investigate, comment, or
  fix.
- **[Batch Automation](batch-automation.md)** — fan out a swarm of
  concurrent sessions to draft, critique, refine. The `AsyncClient` makes
  ten or a hundred parallel runs trivial.

The research-fleet example from the landing page is a special case of
[Batch Automation](batch-automation.md): one session per topic, every
session streams findings back to the same page, the UI fills in as the
work completes.

## Wrapping the agent CLI in your own surface

Sometimes you want the same UX as `claude` or `codex` from the terminal,
but pointed at a hosted runtime instead of a local one.

- **[CLI Wrapper](cli-wrapper.md)** — a thin client that takes a prompt
  on stdin, prints the stream on stdout, and lives entirely on top of
  AOD's three calls.

## Building your own pattern

The recipe is always the same:

1. Define an [agent](../api/concepts.md) once. Version it as you tune the
   system prompt.
2. Define an [environment](../api/concepts.md) per use case — packages,
   secrets, network policy.
3. On every event, [create a session](../api/quickstart.md) and stream
   the result.

If your pattern doesn't fit any of the categories above, that's a feature
request — open an issue at
[ravi-hq/agent-on-demand](https://github.com/ravi-hq/agent-on-demand/issues),
or send a PR for a new pattern page.
