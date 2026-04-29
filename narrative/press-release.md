# Agent on Demand Lets Developers Spawn AI Agents Like Database Rows

**A three-resource REST API eliminates months of infrastructure work, bringing long-running, sandboxed, streaming agents into any application with a single POST request**

*FOR IMMEDIATE RELEASE*

---

**SAN FRANCISCO, April 29, 2026** — Today, Agent on Demand announced the general availability of its REST API for spawning, managing, and streaming AI agents — making it possible for any developer to add long-running, sandboxed, tool-using agent workflows to their application without building or owning agent infrastructure.

With Agent on Demand, developers define an agent once, send it a prompt, and read its work streaming back over Server-Sent Events in real time — the same way they'd write a row to a database or enqueue a background job. Sandboxing, secrets management, multi-turn session persistence, streaming, and lifecycle cleanup are handled entirely by the API.

---

## The Problem Developers Were Solving Before This Existed

Building a meaningful agent feature into a production application has meant building infrastructure first: provisioning isolated sandboxes, encrypting and injecting per-user secrets, designing a streaming protocol, tracking multi-turn session state, and handling lifecycle cleanup when sessions complete or fail.

For most teams, this is a months-long infrastructure project before a single user-facing feature ships.

Solo builders hit the infrastructure wall and stop. Platform teams ship something fragile after a quarter and become permanently on-call for it. The teams that do it right spend eighteen months on plumbing — and then maintain that plumbing indefinitely, with every new product feature subject to what the infrastructure supports.

The result: agent-powered applications have been accessible only to teams with significant infrastructure resources. The kanban board that opens PRs when a ticket moves. The support inbox that investigates tickets before a human touches them. The research tool that fans out a fleet of agents and synthesizes the results in real time. These experiences have been technically possible for over a year. The infrastructure cost has kept most teams from starting.

---

## What Agent on Demand Built

Agent on Demand is a three-resource REST API:

**Agents** are the recipe. A developer defines a model, runtime, system prompt, tools, MCP servers, and skills once — versions it, and reuses it across as many sessions as needed. Updating an agent doesn't break existing sessions; version history is preserved.

**Environments** are the sandbox. A developer declares the packages to install, secrets to inject (encrypted at rest, never returned by the API), setup scripts to run, and network policy to apply. That declaration is applied automatically every time a session provisions — no manual setup, no per-session ops work.

**Sessions** are the run. One agent in one environment, executing against a prompt. Output streams back over Server-Sent Events in real time — provisioning stages, stdout, exit status. Developers can continue the conversation with a follow-up prompt; the filesystem and agent context persist between turns. Every turn is logged and replayable from any cursor position.

A developer with an API token can spawn a fully sandboxed, tool-using agent — with streaming output reaching their UI — in under a minute.

---

## What Developers Are Building

Teams using Agent on Demand have shipped:

**Project management integrations** where moving a ticket to "In Progress" spawns an agent that reads the ticket, writes code against the relevant codebase, and opens a PR — with the card moving itself to "In Review" when the agent finishes. What was a multi-month infrastructure project is now a weekend integration.

**Support inbox automation** where every new ticket spawns an investigator agent that attempts to reproduce the issue, documents its findings, and pre-tags the ticket with severity and component — before a human touches it. Support teams report handling significantly higher ticket volume with the same headcount.

**Batch research pipelines** where concurrent agent sessions fan out across data sources — competitor sites, regulatory filings, customer reviews — and synthesize results in parallel. Research that took days of manual work now completes overnight.

**Internal dashboards** where teams access shared agent capabilities without distributing individual model API keys. A single service token, a FastAPI proxy, and any team member can run an agent session through a browser.

**Slack bots** where each thread maps to a persistent agent session. The agent remembers the conversation, the files it has worked with, and the context from previous turns — because the Sprite filesystem is still warm between messages.

---

## What Developers Say

> "I spent three months trying to build sandboxed agent execution myself. I got something that mostly worked — leaked sessions on termination, dropped streams under load, broke when two users hit it simultaneously. Then I saw Agent on Demand and had a working session streaming to my UI in eight minutes. The thing I spent a quarter building is now a POST request."

— Developer, early access

> "We'd been putting off the agent feature because every scoping conversation ended with 'and then we'd need a platform team to own the infra.' Agent on Demand removed that blocker entirely. We shipped in a sprint."

— Engineering lead, B2B SaaS

---

## How It Works

Agent on Demand runs sessions on Sprites — isolated virtual machines with full tool access: bash execution, file reading and writing, web search, and any MCP servers the developer configures. Secrets are encrypted with Fernet and never returned in API responses. Sessions stream output over SSE with heartbeats every fifteen seconds and replay support from any log cursor position.

The API supports four runtimes: Claude (Anthropic), Codex (OpenAI), Gemini (Google), and opencode. Developers bring their own model API keys. No model costs are bundled into the API pricing.

Typed SDKs ship for Python (`aod-sdk` on PyPI) and TypeScript (`@ravi-hq/aod-sdk` on npm), with synchronous and asynchronous clients and full streaming support. Reference implementations are provided for the four most common integration patterns: CLI wrapper, Slack bot, web dashboard, and batch automation harness.

Agent on Demand is open-source under the Apache 2.0 license. Developers can run the full stack locally with `make dev`, deploy it on any cloud platform using the provided Render configuration, or use the hosted API at aod.ravi.id.

---

## Availability

Agent on Demand is available today.

- **Hosted API:** aod.ravi.id — sign up, add a model key, stream a session in under a minute
- **Documentation:** ravi-hq.github.io/agent-on-demand — quickstart, API reference, and integration patterns
- **Source code:** github.com/ravi-hq/agent-on-demand — Apache 2.0, CI-gated, mutation-tested
- **Python SDK:** `pip install aod-sdk`
- **TypeScript SDK:** `npm install @ravi-hq/aod-sdk`

---

*About Agent on Demand: Agent on Demand is an open-source REST API for spawning, managing, and streaming AI agents. Built by Ravi HQ and licensed under Apache 2.0, it is available as a self-hosted deployment or as a managed service at aod.ravi.id.*
