# Patterns

Common shapes for building on top of Agent on Demand. Each pattern is a
self-contained recipe — pick the one that matches what you're building and
adapt the example to your stack.

| Pattern | When to use it |
|---------|----------------|
| [CLI Wrapper](cli-wrapper.md) | A one-liner that kicks off an agent task from the terminal — create a session, stream output, exit. |
| [Chat Bot](chat-bot.md) | Slack, Discord, or in-app chat where each thread maps to one multi-turn session. |
| [CI Bot](ci-bot.md) | GitHub Actions or other CI runs that review PRs, suggest fixes, or post analysis comments without a human in the loop. |
| [Internal Dashboard](dashboard.md) | A web UI that lets multiple users kick off and monitor sessions through your own auth layer. |
| [Batch Automation](batch-automation.md) | Run the same agent task across many inputs concurrently, staying within Sprites and runtime quotas. |

Python examples in these patterns use the official
[`aod-sdk`](../sdks/python.md) package (`pip install aod-sdk`). TypeScript
equivalents use [`@ravi-hq/aod-sdk`](../sdks/typescript.md).
