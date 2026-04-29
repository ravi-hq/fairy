# Story Page — Agent on Demand

---

## The Two-Liner

The next generation of apps won't have an "AI features" tab.
They'll have agents doing real work — spawning on demand, running in their own sandboxes, streaming results back while your users watch.

Building that just became a three-resource REST API.

---

## Elevator Pitch

For two years, "AI in your product" meant one thing: call an LLM, stream tokens, render a chat bubble. Easy to add. Forgettable to use.

The agents that actually matter don't chat — they do work. They have tools. They have a filesystem. They run for minutes. They produce real artifacts — diffs, reports, briefings. You've seen the prototypes in developer tooling: Claude Code opens PRs, Codex runs test suites, Gemini CLI reads entire codebases. The question isn't whether this pattern will show up in everyday software. It already is. The question is who builds it first.

The problem is the infrastructure. Spawning a long-running, sandboxed, tool-using agent today means provisioning compute, managing encrypted secrets, inventing a streaming protocol, handling multi-turn session state, and dealing with cleanup. It's not an afternoon of app code. It's months of ops work — before you write a single feature.

Agent on Demand collapses all of that to a three-resource REST API: define an agent, hand it a prompt, read the stream. No infrastructure to provision. No lifecycle to manage. No ops team to hire. Agents become part of your app's surface area — not a side project that owns your calendar.

---

## Big Picture Vision

Software is being rewritten from the inside. Not at the layer of features or UX patterns — at the level of what apps are capable of doing on behalf of their users.

For a decade, the most ambitious software competed on data and personalization. The best apps knew you better than you knew yourself — recommending, ranking, surfacing. Powerful. But the app still waited for you to act. It was a very smart assistant that couldn't actually do anything.

The next wave acts. A project management tool that doesn't just track the state of a ticket — it opens the PR when the ticket moves. A support platform that doesn't just route tickets — it investigates them before a human touches them. A research tool that doesn't just surface documents — it synthesizes them into a briefing while you get coffee.

These aren't chatbots with a better UI. They're applications where agents are woven into the core flow: triggered by events, doing real work in real sandboxes, streaming results back in real time, changing state when they're done.

The teams building this experience will redefine their categories. The teams that bolt a chat bubble onto their sidebar will look like the dial-up websites of 1999.

Agent on Demand provides the infrastructure layer that makes this a product decision, not an infrastructure project. Three resources — agents, environments, sessions. One POST per turn. Results streaming over SSE while your UI updates in real time. Bring your own model keys. Multi-provider: Anthropic, OpenAI, Google. Open-source, Apache 2.0, no lock-in.

This is the world where the kanban-that-ships-PRs is a weekend hack, not a Series A. Where house-hunting-with-a-research-fleet is a feature, not a company. Where the support inbox that investigates tickets before humans touch them is a sprint, not a quarter.

Any developer who can write a POST request can now ship the kind of product that was, one year ago, the headline of a TechCrunch article.

The primitive is here. The only question is what gets built on top of it.
