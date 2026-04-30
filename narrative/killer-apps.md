# Three Killer Applications

*Three product pitches for applications that are only now possible — built on an API that makes spawning a long-running, sandboxed, tool-using AI agent as simple as writing a database row.*

---

## 1. The Board That Ships

### Your project management tool should open its own PRs.

---

**The pain every engineering team knows**

A ticket sits in "Ready." A developer picks it up, reads it, opens their editor, finds the right files, writes the code, opens the PR, links it back to the ticket, and moves the card. This takes hours on a good day. The ticket and the work are tracked in two different systems that never talk to each other except through human copy-paste.

The gap between "this is planned" and "this has a draft PR" is where engineering velocity goes to die.

**The insight**

What if the ticket moving to "In Progress" was the trigger — and a PR appearing in GitHub was the result?

Not a template. Not a scaffold. An agent that reads the ticket, clones the repo, finds the relevant code, writes a meaningful implementation, runs the tests, and opens a pull request. The card moves itself to "In Review" when the agent finishes. The developer reviews code instead of writing boilerplate.

**What you build**

A project management tool — kanban, linear-style, whatever — where every ticket has an "agent" button or an automatic trigger on status change. The agent is spawned with the ticket body as its prompt, the repo URL in its environment, and a GitHub token in its secrets. It works in a persistent sandbox: it can clone the repo, explore the codebase across multiple turns, write and rewrite until tests pass, and push.

The UI shows the agent working in real time — files opened, commands run, tests executed — streamed back over SSE while the user watches. When the agent completes, the PR URL appears on the ticket. If the agent gets stuck, the developer can continue the session with a follow-up prompt. The filesystem is still warm.

**Why this wasn't possible before**

A single API call to an LLM can't do this. It doesn't have a filesystem. It can't run tests. It can't explore a large codebase across multiple steps and remember what it found. It times out in seconds, not minutes.

The piece that was missing was a runtime primitive: a long-running, sandboxed process with real tools and persistent state that you could spawn from your app like any other background job. That primitive now exists.

**The build, briefly**

```python
# Ticket moves to In Progress
session = client.sessions.create(
    agent_id=coding_agent.id,       # claude-code runtime, repo tools configured
    environment_id=env.id,           # GITHUB_TOKEN encrypted in environment
    prompt=f"Implement this ticket:\n\n{ticket.body}\n\nRepo: {repo_url}"
)

# Stream agent output back to the ticket view
for event in client.sessions.stream(session.id):
    push_to_ticket_activity_feed(ticket.id, event)

# On exit, attach PR URL to ticket
ticket.update(pr_url=extract_pr_url(session), status="In Review")
```

**The market**

Every engineering team using a project management tool is the market. The teams that ship this win because their users stop thinking of the tool as a tracker and start thinking of it as an execution engine. Tickets don't pile up. PRs appear. Velocity becomes measurable in a way it never was before.

---

## 2. Scout

### When the alert fires, Scout wakes up first.

---

**The pain every on-call engineer knows**

It's 2:47am. PagerDuty fires. You wake up, find your phone, dismiss the noise, open your laptop. You pull up Grafana. You grep logs. You check the recent deploy. You trace the request. Twenty minutes later, you have a hypothesis. If you're lucky, you also have a fix.

The investigation — the part between "alert fired" and "I know what's wrong" — is almost entirely mechanical. It follows a playbook. It reads logs. It checks the same dashboards. It traces the same paths.

No human should have to do that at 3am.

**The insight**

What if the alert spawned an agent that ran the entire investigation playbook — and posted its findings to Slack before the on-call engineer had even unlocked their phone?

The engineer wakes up to: "Error rate spike on `/api/checkout`. Traced to a 503 from the payments service. Payments service started failing at 02:43:17, three minutes after the `v2.4.1` deploy. Blast radius: ~12% of checkout attempts. Suggested first step: rollback or disable the new payment retry logic."

They don't investigate. They decide.

**What you build**

An incident response tool — or an integration into existing alerting workflows — where every PagerDuty/OpsGenie alert spawns a Scout session. The agent has access to whatever your team has wired up: a log query tool, a metrics API, a deployment history endpoint, a runbook reader. It follows a structured investigation: reproduce the symptom, identify the scope, trace to a cause, check for recent changes, form a hypothesis.

The output is a structured brief in Slack: timestamp, affected surface, probable cause, evidence, suggested action, session link for the on-call to continue if they need to dig deeper.

When the engineer picks up the page, they're not starting an investigation. They're reviewing one.

**Why this wasn't possible before**

Alert webhooks already exist. The problem was never triggering the agent. The problem was that there was no agent runtime capable of doing real investigative work: running bash commands, querying APIs, reading logs, spending several minutes exploring before forming a conclusion. LLM calls are stateless and fast. Investigation is stateful and slow.

Scout needs a process that runs for two to five minutes, uses real tools, and produces an artifact. That's a session, not an API call.

**The build, briefly**

```python
# PagerDuty webhook fires
@app.post("/webhook/pagerduty")
async def handle_alert(alert: Alert):
    session = client.sessions.create(
        agent_id=scout_agent.id,          # configured with log/metrics MCP servers
        environment_id=prod_env.id,        # API keys for your observability stack
        prompt=build_investigation_prompt(alert),
        timeout=300                         # 5 minutes to investigate
    )

    # Stream findings into a Slack thread as they appear
    async for event in client.sessions.stream(session.id):
        if event.type == "output":
            await slack.append_to_thread(alert.thread_ts, event.data)

    # Final structured brief posted when agent finishes
    await slack.post_summary(alert.thread_ts, session.id)
```

**The market**

Every engineering team with on-call rotation is the market. The pitch is simple: Scout doesn't replace your engineers. It means your engineers sleep better. The first time someone wakes up to a solved incident instead of an empty Slack thread, Scout sells itself.

---

## 3. Brief

### A research team in your product. Not a search bar. A team.

---

**The pain that scales with information**

There's a category of knowledge work that follows the same pattern at every company: gather information from multiple sources, synthesize it into a coherent picture, deliver it to someone who needs to make a decision.

Due diligence on an acquisition target. Competitive analysis before a product launch. Background research before a sales call. Neighborhood research before a house purchase. Literature review before a clinical decision. Investment analysis before writing a check.

Today, this work is done by humans — often expensive ones — because no tool can actually do it. Search engines surface documents. Chatbots summarize text. But neither can fan out across sources, reason about what's missing, decide what to investigate next, and synthesize a coherent brief. That requires something that can run for a while, use real tools, and maintain context across a multi-step investigation.

**The insight**

What if your product could spawn a research fleet — concurrent agents investigating different dimensions of a question in parallel — and stream their findings back as a live, assembling brief?

The user drops a company name. One agent researches their product and pricing. Another reads their recent press and job postings. A third searches customer reviews and forums. A fourth checks the founding team's backgrounds. The brief assembles in real time as each agent returns, synthesis happening as the final agent finishes.

Not a search. Not a summary. A researched opinion.

**What you build**

A research product for any domain where the unit of work is "give me everything I need to know about X." The user submits a target and a context. The application fans out a configurable number of concurrent agent sessions — each one assigned a specific research dimension — then collects and synthesizes their outputs into a structured brief.

The UI is the brief assembling in real time: sections appearing as agents complete, sources cited inline, gaps flagged, a synthesis section that writes itself last. Users can drill into any section and ask follow-up questions — opening a new session with the context of what was already found.

The product works for any domain where research is valuable and time-consuming: sales intelligence, real estate, M&A, competitive analysis, hiring, venture investing, policy research, academic literature review.

**Why this wasn't possible before**

The fan-out pattern — multiple concurrent long-running agents, each with real tools, streaming results back as they complete — is not achievable with single API calls. You need a session primitive: isolated, parallel, streaming, with enough runtime to do real web research across multiple sources.

The synthesis step — a final agent reading all the intermediate outputs and writing a coherent brief — requires multi-turn: the filesystem where intermediate results landed is still warm when the synthesis agent starts.

**The build, briefly**

```python
# User submits a research request
dimensions = ["product_and_pricing", "team_and_history",
               "customers_and_reviews", "market_and_competitors"]

# Fan out concurrent research sessions
research_sessions = await asyncio.gather(*[
    client.sessions.create(
        agent_id=researcher_agent.id,
        environment_id=research_env.id,   # web_search + web_fetch tools enabled
        prompt=build_research_prompt(target, dimension)
    )
    for dimension in dimensions
])

# Stream each session's output into its brief section in real time
await asyncio.gather(*[
    stream_to_brief_section(session, dimension)
    for session, dimension in zip(research_sessions, dimensions)
])

# Synthesis: spawn one more agent with all findings as context
synthesis = client.sessions.create(
    agent_id=synthesizer_agent.id,
    prompt=f"Synthesize these research findings into a brief:\n\n{combined_findings}"
)
```

**The market**

The research-as-a-product market is currently served by human analysts, expensive SaaS tools, and DIY prompt chains that produce shallow results. Brief wins because it actually does the work — not a summary of search results, but a structured investigation with sources, gaps, and a reasoned synthesis. The first vertical to nail this becomes the default tool for that domain.

---

*These three applications share a common foundation: a runtime primitive that makes long-running, sandboxed, tool-using agents as easy to spawn as a background job. The infrastructure exists. The applications don't yet — which means the window is open.*
