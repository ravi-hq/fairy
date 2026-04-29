# Agent on Demand — Narrative Deck

---

## Slide 1 — Title

# Agents Are the New Compute

### The infrastructure to build agent-powered apps — without building infrastructure

---

## Slide 2 — The World Changed

# Two Years Ago vs. Today

**Two years ago**, "AI in your product" meant one thing:
Call an LLM. Stream tokens. Render a chat bubble.

**Today**, the agents that matter don't chat. They do work.

- They have tools — bash, file editing, web search
- They have a filesystem that persists between turns
- They run for minutes, not milliseconds
- They come back with an artifact — a diff, a report, a briefing — not a paragraph

**You've already seen the prototypes: Claude Code. Codex. Gemini CLI.**
These aren't dev toys. They're the template for what everyday software will do next.

---

## Slide 3 — The New Expectation

# Users Will Expect This Everywhere

Not in IDEs. **Everywhere.**

- The kanban board that opens the PR when a ticket moves
- The support inbox that investigates tickets before humans touch them
- The research tool that builds the briefing while you get coffee
- The sales tool that researches the prospect before the rep opens the record

Apps that ship these experiences will redefine their categories.
Apps that bolt on a chat sidebar will look like dial-up websites.

**The question isn't whether this happens. It's who builds it first.**

---

## Slide 4 — Archetype 1: The Solo Builder

# "I Just Wanted to Add One AI Feature"

**The estimate:** a weekend.

**What actually happened:**

1. Users won't stare at a spinner — need streaming → build an SSE layer
2. The agent needs tools — need a runtime, not just an API call → research sandboxing
3. Per-user API keys can't live in env vars → design and build a secrets layer
4. Users need to follow up → session filesystem needs to persist between turns
5. Sessions don't clean themselves up → add lifecycle management

**Three months in. Zero new features shipped. Infra that sort of works.**

Most solo builders stop here.
The ones who keep going spend another three months hardening it.
By the time it's shippable, the window has moved.

---

## Slide 5 — Archetype 2: The Platform Team

# "We Have the Resources. Nothing Ships."

Two senior engineers. One month of runway. Leadership is excited after the demo.

| Week | What they were building |
|------|------------------------|
| 3 | Sandbox isolation — evaluate, choose, integrate |
| 6 | Secrets layer — design, auth integration, security review |
| 9 | Streaming + state management — SSE, reconnects, replay |
| 11 | Multi-turn persistence — filesystem across turns, context survival |
| 14 | **Ship beta** — slow cold starts, known stream drops, session leak bug |

**Result:** Q3 deadline met. "Beta" label. Known issues page.
The platform team is now permanently on-call.
Every new product feature is a negotiation.

---

## Slide 6 — Archetype 3: The Team That Built It Right

# "We Shipped It. Now We Own It Forever."

18 months. 3 engineers. Production-grade, and genuinely proud of it.

✓ Isolated sandboxes  
✓ Encrypted secrets  
✓ Reliable streaming with replay  
✓ Multi-turn session persistence  
✓ Worker queue and observability  
✓ Automated lifecycle cleanup  

**Also:**

- Every model provider update → regression testing
- Every cloud pricing change → evaluation and possible migration
- Every new product request → scoped against what the infra supports
- Three competitors shipping faster — apparently without a platform team

**The system built for last year's requirements is this year's constraint.**

---

## Slide 7 — The Root Cause

# Spawning an Agent Is an Infrastructure Project

Three teams. Three timelines. Three failure modes. One cause.

> Building a long-running, sandboxed, tool-using agent into your app
> is not an API call. **It's a sprint. Or a quarter. Or eighteen months.**

**What the gap costs:**

| Cost | Impact |
|------|--------|
| Speed | Product teams wait on infra teams |
| Scope | Features that are possible never get built |
| Access | Only well-resourced teams can experiment |
| Quality | DIY infra has edge cases production infra doesn't |

**This gap will close. The question is whether you wait for it.**

---

## Slide 8 — The New World

# What If Spawning an Agent Looked Like This?

```python
session = client.sessions.create(
    agent_id=agent.id,
    prompt="Review this PR and file a structured report"
)

for event in client.sessions.stream(session.id):
    print(event)
```

**That's it.**

No sandbox to provision. No secrets to manually inject.
No streaming protocol to design. No lifecycle to manage.
No platform team to hire.

Define the agent once. Send a prompt. Read the stream.
The agent runs in its own sandbox, with its own tools, on its own filesystem.
Results stream to your UI in real time.

---

## Slide 9 — What Gets Built

# The Applications That Become Possible

| Domain | What agents enable |
|--------|--------------------|
| Project management | Ticket moves → agent writes PR → card updates itself |
| Customer support | New ticket → investigator agent → pre-tagged, pre-documented |
| Real estate | Drop a city + priorities → research fleet → personalized map |
| Legal | Upload contract → agent extracts clauses, flags risk |
| Competitive intel | Weekly briefing assembled by agents, no one writes it |
| Sales | New CRM lead → research agent → brief + drafted outreach |
| Incident response | Alert fires → agent investigates → posts to Slack |
| Data pipelines | Quality failure → agent traces anomaly → files report |
| Education | Student session → persistent tutor with memory across sessions |
| Security | PR opened → agent scans diff → structured vulnerability review |

**None of these is a chatbot. All of them are agent-powered applications.**

---

## Slide 10 — Three Resources. One HTTP API.

# Agent on Demand

**Agents** — the recipe
Model, runtime, system prompt, tools, MCP servers, skills.
Define once. Version forever. Share across a million sessions.

**Environments** — the sandbox
Packages, encrypted secrets, setup scripts, network policy.
Declared once. Applied automatically at provision time. Never returned in API responses.

**Sessions** — the run
One agent in one sandbox. Streaming over SSE. Multi-turn with filesystem persistence.
Logged, replayable, terminable.

```
POST /sessions      →  session created, provisioning begins
GET  /sessions/{id}/stream  →  SSE stream, real-time output
POST /sessions/{id}/prompt  →  continue the conversation
```

*Bring your own model keys. Apache 2.0. No lock-in.*

---

## Slide 11 — The Evidence

# It's Real. It Runs. People Are Building on It.

**aod.ravi.id** — hosted API, live now.
Sign up, add a model key, stream a session in under a minute.

**Apache 2.0** — real code on GitHub.
CI-gated migrations. Mutation-tested critical paths. Two-process Django + Procrastinate worker.

**Four runtimes** — Claude, Codex, Gemini, opencode.
Bring your own Anthropic, OpenAI, or Google key.

**Typed SDKs** — `aod-sdk` on PyPI · `@ravi-hq/aod-sdk` on npm.
Sync + async. Full streaming. Same API, Python or TypeScript.

**Reference implementations that ship** — Slack bot, web dashboard, batch pipeline, CLI wrapper.
Not one of them is a chatbot.

---

## Slide 12 — The Transformation

# Before and After

**Before Agent on Demand:**

- Kanban that ships PRs → 9 months of infra → Series A to staff the platform team
- Support inbox that investigates → 6 months → fragile, on-call forever
- Research tool with a fleet of agents → "maybe next year"
- Incident responder that wakes up before you → never prioritized

**After:**

- Kanban that ships PRs → **weekend hack**
- Support inbox that investigates → **one sprint**
- Research tool with a fleet of agents → **a feature, not a company**
- Incident responder → **two POST requests and an SSE stream**

The teams that treat agents as a primitive will define the next wave of software.
The teams that treat agents as an infrastructure project will watch them do it.

---

## Slide 13 — Start Building

# Agent on Demand

**API:** aod.ravi.id
**Docs:** ravi-hq.github.io/agent-on-demand
**Source:** github.com/ravi-hq/agent-on-demand

```
pip install aod-sdk
npm install @ravi-hq/aod-sdk
```

Apache 2.0. Self-host or use the managed API. Bring your own model keys.

**The primitive is here. What are you building?**
