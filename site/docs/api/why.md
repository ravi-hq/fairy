# Why Agent on Demand

## AI agents stopped being a developer tool

For two years, "AI in your product" meant one thing: call an LLM, stream
tokens, render a chat bubble. The agent was a feature you talked *to*.

That game is ending. The agents that matter now don't chat — they *do work*.
They have tools. They have a filesystem. They run for minutes, not seconds.
They go off and accomplish something, then come back with results. Claude
Code, Codex, Gemini CLI, opencode — these aren't dev toys. They're the
prototype of a new kind of compute: **non-deterministic, long-running,
sandboxed processes that produce real artifacts.**

The world is starting to expect this in everyday software. Not in IDEs.
Everywhere.

## What that looks like in product

The next generation of apps won't have a *Sparkles* menu labeled "AI
features." Agents will be woven into the core flow:

- A **kanban board** where dragging a card to *In Progress* spawns Claude
  Code in a clean sandbox. It opens the PR. The card moves itself to *In
  Review* when the agent finishes.
- A **house-hunting app** where a buyer types a city and three wishes, and a
  fleet of research agents fans out to investigate neighborhoods, schools,
  walkability, commute. The buyer's personalized map fills in live as each
  session streams back.
- A **support inbox** where every inbound bug spawns an investigator agent
  that reproduces the issue, files a report, and pre-tags the ticket. Your
  humans see triaged tickets, not raw ones.
- A **batch generation pipeline** that spawns ten agents in parallel to
  draft, critique, and refine a stack of marketing copy — with every turn
  logged, replayable, and terminable.

None of these are coding tools. They're products that use coding agents as
a generic compute primitive.

## The wall in front of every team that wants to build this

Today, to ship any of those experiences, you don't write app code — you
write infrastructure.

You provision sandboxes. You install packages on each one. You manage model
API keys and rotate them safely. You wire up a streaming protocol that
clients can reconnect to. You handle multi-turn state and warm filesystems.
You watch out for orphaned VMs and double-spent agent runs. You build a
worker pool. You write the migration that backfills the new audit table.

You become an ops team for a thing that should have been an API call. Most
teams never start. The ones that do spend nine months not shipping the
product.

## What if agents were a primitive?

Imagine if spawning an agent looked exactly like writing a database row.

You define the agent once: model, runtime, system prompt, tools. You hand a
session a prompt. You read the stream. The agent runs in its own sandbox,
on its own filesystem, with its own keys — and tells you what it did. When
it's done, it's done. When you need to continue the conversation, the
filesystem is still there, waiting.

No compute to provision. No queues to babysit. No lifecycle to manage. No
streaming protocol to invent. No secrets to encrypt by hand. **Agents
become part of your app's surface area — not a side project.**

This is the world where the kanban-that-ships-PRs is a weekend hack, not a
Series A. Where house-hunting-with-a-research-team is a feature, not a
company.

## What's in the box

Three resources, one HTTP API:

- **[Agents](concepts.md)** are the recipe — runtime, model, system prompt,
  MCP servers, skills. Define once, version with optimistic concurrency,
  share across a million sessions.
- **[Environments](concepts.md)** are the sandbox — packages, secrets
  (encrypted at rest, never echoed back), setup scripts, network policy.
  Declare it once, applied automatically when a session starts.
- **[Sessions](concepts.md)** are the run — one agent in one sandbox,
  streaming its work back over [SSE](streaming.md). Continue with another
  prompt; the filesystem is still warm. Every turn logged, replayable,
  terminable.

Bring your own model keys. Bring your own Sprites account, or use ours.
Run it locally with `make dev`, deploy on Render in an hour, or hit the
hosted API at [aod.ravi.id](https://aod.ravi.id). Apache-2.0. No lock-in.

Three calls, one running session. See the [Quickstart](quickstart.md).

## When this is the right tool

- You're building an app or integration where AI agents do real work as
  part of the user-facing flow — kanban automation, research fleets, triage
  bots, batch generation, anything where a non-deterministic process needs
  to run, stream, and produce artifacts.
- You want sessions isolated from each other and from your host
  infrastructure (every session runs in its own Sprite).
- You need multi-turn conversations that preserve filesystem state between
  messages.
- You want to drive agent execution from code, CI, or a backend service —
  not from a developer's terminal.

## When it isn't

- **Local interactive coding.** A developer at a laptop should run Claude
  Code, Codex, or Gemini CLI directly. AOD adds latency that's wasted in
  interactive use.
- **Pure chat-completion features.** If you just need to stream tokens back
  from a model, call the model API directly.
- **Long-lived agent daemons.** Sprites are designed for bursty,
  short-lived work. For an always-on agent, a regular VM is the right fit.

## Self-hosted vs hosted

| | Self-hosted | Hosted ([aod.ravi.id](https://aod.ravi.id)) |
|---|---|---|
| **Sprites account** | You bring your own | Managed for you |
| **Model API keys** | Stored encrypted in your DB | Stored encrypted, scoped per user |
| **Infra ops** | You run web + worker + Postgres | Handled by the operator |
| **Data residency** | Under your control | Determined by the operator |
| **Cost** | Sprites + model API costs | Sprites + model API costs (no markup) |

Same codebase. Same API. Same SDKs.
