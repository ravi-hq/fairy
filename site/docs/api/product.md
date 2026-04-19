# Overview

## What is Agent on Demand?

Agent on Demand is a REST API for running AI coding agents on [Sprites](https://fly.io/docs/machines/) — Fly.io's lightweight, fast-booting Firecracker microVMs. It manages three resources — agents, environments, and sessions — and exposes a simple HTTP interface for launching agent runs, streaming their output, and continuing multi-turn conversations.

## What problems does it solve?

**Persistent, reusable agents.** Instead of re-configuring model, runtime, system prompt, MCP servers, and skills on every request, you define an agent once and reference it by ID. A hundred sessions can share the same agent template.

**Managed environments.** Package installation, environment variable injection, network policy, and setup scripts are declared on an environment object and applied automatically when a session starts. You don't write that scaffolding into every prompt.

**Multi-turn sessions.** After an agent finishes a turn, the Sprite and its filesystem stay alive. A follow-up prompt via `POST /sessions/{id}/prompt` continues the conversation with full history and the same working directory — no need to re-clone repos or re-install packages.

**Streaming output.** Session output arrives as Server-Sent Events over a persistent HTTP connection. Reconnecting replays all buffered output from the start, so clients that drop the connection don't lose data.

## How it differs from running agents directly

Agent on Demand runs agents on Sprites, not on Fly Machines. Sprites are purpose-built for short-lived, fast-booting workloads. Agent on Demand manages their lifecycle — creating the Sprite at session start, applying the environment, running the agent CLI, and tearing it down on termination — so you don't have to.

The result is a clean API surface: create agent → create session → stream output → optionally continue or terminate.
