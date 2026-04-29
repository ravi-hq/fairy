# The Agent Infrastructure Problem

*This document describes a problem, not a product. Its purpose is to map the shape of an emerging gap in software development and spark thinking about what could be built if that gap were closed. No solution is proposed here.*

---

## The World Has Already Changed

Something shifted in developer tooling over the last eighteen months, and most application developers haven't felt it yet.

The shift is this: AI agents stopped being a novelty. They became productive. Developer tools now ship agents that open PRs, run test suites, read entire codebases and propose refactors. These tools are in daily use by developers who care about output, not hype.

What they have in common: they are not chatbots. They don't answer questions. They do work. They have tools — bash, file editing, web search. They have a filesystem. They run for minutes, not seconds. They come back with an artifact — a diff, a file, a report — not a paragraph of text.

This is the prototype of a new kind of compute. Long-running. Non-deterministic. Sandboxed. Streaming. And it's coming to everyday software — to the kanban boards, support inboxes, research platforms, and data pipelines that most developers spend their careers building.

The question is: when it arrives, who will be ready to build it?

---

## Three Developers Walk Into an Infrastructure Problem

### The Solo Builder

You're building a product. Maybe it's a project management tool. Maybe a customer support platform, or something for legal teams. You have users, some traction, and a product instinct that's been right before.

Then you see a demo — someone's kanban board where dragging a ticket to "In Progress" spawns an agent that reads the ticket, understands the codebase, writes actual code, and opens a PR. The card moves itself to "In Review" when the agent is done. Your users would love this. You figure it's a weekend.

It isn't.

You start with the obvious path: call the model API, get a response. But your users won't wait 45 seconds staring at a spinner. They want to see the agent working. So you need streaming. You stand up an SSE endpoint. You realize you need somewhere to store partial results. You add a table.

Then you realize the agent needs tools — it has to read files, write code, call external APIs. That means a runtime, not just a model call. You research sandboxing. You look at containers, serverless functions, isolated VMs. You spend two weeks on the "just run it somewhere safely" problem.

Then your agent needs secrets — GitHub tokens, API keys that are different per user. You can't put those in environment variables. You design a secrets layer. You encrypt things. You realize you've accidentally built a miniature Vault.

Three months in, you haven't shipped a single user-facing feature. You have an infrastructure layer. You have a sandbox that sort of works. You have an agent that can sort of run. You're exhausted, and you're not done.

Most solo builders stop here. The ones who don't spend another two to three months hardening what they built — fixing edge cases, handling cleanup, making the streaming reliable under load, adding the multi-turn state management they forgot about. By the time they have something shippable, the window has moved.

---

### The Platform Team

You're at a mid-size company with real engineering resources. Leadership wants "agent capabilities" in the product by Q3. You assign a platform team: two senior engineers, a reasonable runway. They do a spike. It goes well. There's a demo. Leadership is excited.

Then they start building the thing that will actually power the feature in production.

**Week 3:** Sandbox isolation. The agent needs its own environment — it can't run on the web server. The team evaluates several options. Each has a steep learning curve. They pick one. Integration takes longer than the spike suggested.

**Week 6:** Secrets management. Per-user API keys can't live in config files. The team designs a secrets layer integrated with the existing auth system. They write a key rotation plan. Security reviews it. Changes are requested.

**Week 9:** Streaming and state management. The product team wants real-time output — users should see the agent working, not wait for a result. Standing up SSE is fast. The state management behind it — resuming interrupted streams, handling client disconnects, replaying missed events — takes two more weeks.

**Week 11:** Multi-turn sessions. Users need to follow up with the agent. The session needs to keep its filesystem between turns. The agent's context needs to survive the gap. This is harder than it looked in the spike.

**Week 14:** Q3. They ship something. It's slow to provision on cold starts, occasionally drops streams, doesn't handle concurrent users gracefully, and there's a known bug in the cleanup process that leaks idle compute. The product team ships it with a "beta" label and a known issues page.

The platform team is now permanently on-call for the infrastructure they built. Every product feature that touches agents requires a negotiation with the platform team about what the infra supports. The original demo — the exciting one that got everyone aligned — is buried under nine months of plumbing.

---

### The Team That Built It

You did it right. Eighteen months, three engineers, proper architecture reviews. You have isolated sandboxes, encrypted secrets, reliable streaming, multi-turn persistence, a worker queue, observability, session lifecycle management, and automated cleanup. It runs. Users use it. You're proud of what the team built.

Here's what you also have: a system to maintain indefinitely.

Every model provider update requires regression testing. Every cloud infrastructure change — pricing, availability zones, networking policies — requires evaluation and sometimes migration. Every security audit touches your secrets layer. Every new product feature is scoped against what the infra supports, and sometimes it doesn't support the thing the product team wants.

Three competitors are shipping faster. One of them, somehow, doesn't appear to have a platform team at all.

The system built for last year's requirements is this year's constraint.

---

## The Shape of the Gap

Three different teams. Three different timelines. Three different failure modes. One underlying cause:

**Spawning a long-running, sandboxed, tool-using AI agent is not an API call. It's an infrastructure project.**

For most application developers — the people building the kanban boards and support platforms and research tools and data pipelines — infrastructure projects are not what they're staffed for, excited by, or resourced to maintain.

This gap is not permanent. Infrastructure problems always get abstracted away. Databases became services. Compute became serverless. Auth became a library and a SaaS. Search became an API call. Every layer that was once a project is now a primitive.

The same thing will happen to agent execution. The question is when, and what gets built in the meantime by the teams who don't want to wait.

**What the gap costs today:**

- **Speed** — teams that should be shipping product spend months on plumbing
- **Scope** — features that are technically possible never get prioritized because the implementation cost is too high
- **Access** — only well-resourced teams can afford to experiment; smaller teams are locked out
- **Quality** — DIY infrastructure has edge cases that production-hardened infrastructure doesn't: stream drops, session leaks, race conditions on concurrent turns

---

## What Could Exist If the Gap Were Closed

If spawning a sandboxed, streaming, multi-turn AI agent were as simple as writing a database row — not a weekend project, not a sprint, not a quarter, but a single API call — what would developers build?

These are sparks, not specs. Questions worth sitting with.

---

**In project management:**
What if a ticket moving to "In Progress" could open a draft PR — not by triggering a template, but by spawning an agent that reads the ticket, understands the codebase, and writes actual code? What if the card moved itself to "In Review" when the agent finished? What would your backlog look like if the gap between "planned" and "drafted" was ten minutes instead of two days?

**In customer support:**
What if every new support ticket spawned an investigator that tried to reproduce the issue, documented what it found, and pre-tagged the ticket with severity and component — before a human touched it? What would that do to the economics of a support team? What would it do to response times?

**In real estate and research-heavy consumer products:**
What if a house hunter could drop a city and a list of priorities — walkability, school quality, commute time, neighborhood feel — and a fleet of agents fanned out to research each dimension in parallel, drawing a personalized map in real time? Not a filter on a generic dataset. An investigation tailored to this buyer.

**In legal and compliance:**
What if contract review meant uploading a PDF and watching an agent extract clauses, flag non-standard terms, surface risk, and generate a summary — not in a day, but in under a minute? What changes about the unit economics of legal work when the first pass is free?

**In competitive intelligence:**
What if your product team received a weekly briefing assembled by agents — tracking competitor releases, pricing changes, customer reviews, job postings — without anyone having to write a report? What decisions get made faster when the context is always current?

**In sales:**
What if every new lead in your CRM spawned a research agent that built a company brief, identified the right contact, and drafted a personalized first message — before the rep even opened the record? What's the conversion rate on outreach that's actually researched?

**In incident response:**
What if a monitoring alert triggered an agent that investigated the incident — checking logs, tracing requests, identifying the blast radius, correlating with recent deploys — and posted structured findings to Slack while the on-call engineer was still waking up? How many incidents get resolved before anyone's adrenaline spikes?

**In data quality and pipelines:**
What if a failed data quality check spawned an agent that identified the anomaly, traced it to a source system, and filed a structured report — not as a scheduled job that runs overnight, but as a reactive investigation that completes in the same window as the failure?

**In education:**
What if every student had a tutoring agent that remembered their previous sessions, tracked what they'd struggled with last time, and adapted its approach accordingly — not a stateless chatbot that forgets everything between conversations, but a persistent collaborator?

**In security:**
What if every pull request triggered an agent that scanned the diff for vulnerability patterns, checked for accidentally exposed secrets, and left a structured review comment — not a linter running static rules, but an investigator reasoning about intent and context?

**In content and publishing:**
What if a content brief could spawn an agent that researches the topic, synthesizes sources, and produces a structured first draft — not autocomplete finishing your sentences, but a researcher handing you something to react to?

**In healthcare administration:**
What if patient intake forms were processed by agents that extracted relevant history, flagged contradictions, and prepared a structured brief for the clinician — before the appointment started, not after?

**In developer tooling beyond IDEs:**
What if any internal tool — a deployment dashboard, a cost explorer, a feature flag manager — could spawn an agent to investigate anomalies, generate explanations, or propose changes? Not as a chat sidebar. As a native action.

---

These are not AI features. They are agent-powered applications. The distinction is important.

An AI feature is a menu item. A chat sidebar. A Sparkles button that summarizes something. It's useful at the margin.

An agent-powered application is a product where agents are woven into the core flow — triggered by events, doing real work in real sandboxes with real tools, streaming results back, changing state when they finish. The agent isn't a feature you talk to. It's part of how the product works.

The developers who build these applications will look back and wonder why it ever seemed hard.

---

*This document describes a problem, not a solution. If you are building something in this space — or thinking about starting — the shape of the gap is the starting point, not the destination.*
