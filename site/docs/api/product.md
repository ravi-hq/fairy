# Overview

## What is Agent on Demand?

Agent on Demand is a REST API for running AI coding agents on [Sprites](https://sprites.dev) — Fly.io's lightweight, fast-booting Firecracker microVMs. It manages three resources — agents, environments, and sessions — and exposes a simple HTTP interface for launching agent runs, streaming their output, and continuing multi-turn conversations.

## What problems does it solve?

**Persistent, reusable agents.** Instead of re-configuring model, runtime, system prompt, MCP servers, and skills on every request, you define an agent once and reference it by ID. A hundred sessions can share the same agent template.

**Managed environments.** Package installation, environment variable injection, network policy, and setup scripts are declared on an environment object and applied automatically when a session starts. You don't write that scaffolding into every prompt.

**Multi-turn sessions.** After an agent finishes a turn, the Sprite and its filesystem stay alive. A follow-up prompt via `POST /sessions/{id}/prompt` continues the conversation with full history and the same working directory — no need to re-clone repos or re-install packages.

**Streaming output.** Session output arrives as Server-Sent Events over a persistent HTTP connection. Reconnecting replays all buffered output from the start, so clients that drop the connection don't lose data.

## How it differs from running agents directly

Agent on Demand runs agents on Sprites, not on Fly Machines. Sprites are purpose-built for short-lived, fast-booting workloads. Agent on Demand manages their lifecycle — creating the Sprite at session start, applying the environment, running the agent CLI, and tearing it down on termination — so you don't have to.

The result is a clean API surface: create agent → create session → stream output → optionally continue or terminate.

## When to use it

- You want to trigger agent sessions from code or CI, not from a developer's terminal.
- You're building an integration — Slack bot, GitHub Action, internal dashboard, batch job — and need a reliable HTTP interface to drive agent execution.
- You want sessions isolated from each other and from your host infrastructure (each runs in its own Sprite).
- You need multi-turn conversations that preserve filesystem state between messages.

## When not to use it

- **Local, interactive coding**: if a developer is sitting at their laptop, they should run Claude Code, Codex, or Gemini CLI directly. Agent on Demand adds latency and infrastructure overhead that's unnecessary for interactive use.
- **Very short-lived tasks with no isolation need**: Sprite provisioning takes a few seconds. If you're running sub-second tasks where sandbox isolation doesn't matter, a direct API call to the model is faster and cheaper.
- **Tasks that need a persistent, long-lived machine**: Sprites are designed for bursty, short-lived work. For a continuously-running agent daemon, a regular VM or container is a better fit.

## Self-hosting vs the hosted API

Agent on Demand is open source (Apache-2.0). You can run your own instance — see the [Deploy Guide](../operators/deploy.md) — or use the hosted API at [aod.ravi.id](https://aod.ravi.id).

| | Self-hosted | Hosted API |
|---|---|---|
| **Sprites account** | You bring your own | Managed for you |
| **Model API keys** | Stored encrypted in your DB | Stored encrypted, scoped per user |
| **Infra ops** | You run web + worker + Postgres | Handled by the operator |
| **Data residency** | Under your control | Determined by the operator |
| **Cost** | Sprites + model API costs | Sprites + model API costs (no markup on API) |

Both options use the same codebase and the same API surface.
