# example-cli

A minimal Python CLI built on [`aod-sdk`](../../clients/python/) that runs one
prompt through Agent on Demand and streams the output. Meant to replace team
aliases like `claude --dangerously-skip-permissions -p "<prompt>"` with a
version that runs inside a Sprite sandbox against a pinned agent, tool set,
and repo list.

```
$ pip install aod-sdk
$ export AOD_API_URL=https://aod.example.com
$ export AOD_API_TOKEN=aod_xxxxxxxx
$ ./example-cli.py "what does /workspace/fairy do?"     # new session
$ ./example-cli.py --session <uuid> "open a PR"          # continue that session
# session 8f3a...
⠋ creating sandbox · 12s       # spinner updates as each provision stage starts
✓ creating sandbox · 2.1s      # one line per completed stage (stderr)
✓ writing env file · 0.2s
✓ writing git credentials · 0.2s
✓ installing packages, cloning repos, running setup · 6.8s
✓ writing mcp config · 0.2s
⠋ starting agent · 10s         # runtime_start — clears on first output
⚙️  Session init · model=claude-sonnet-4-6, tools=27, mcp=[context7]

💭 Let me explore /workspace/fairy to understand what it does.

🤖 Agent · Explore /workspace/fairy codebase

  🔧 Bash · find /workspace/fairy -type f -name "*.md" | head -20

  📖 Read · /workspace/fairy/README.md

✉️  /workspace/fairy is a Django REST API for running AI coding agents...

✨ Done · agent 12.3s, 15 turns, tokens $0.0234
```

## Files

| File | Role |
|---|---|
| [`example-cli.py`](./example-cli.py) | Config + `main()`. The file you fork per team. |

HTTP + SSE are handled by [`aod-sdk`](../../clients/python/) (`Client`, `client.sessions.stream(...)`). Claude `stream-json` is parsed by `aod.pretty.claude.ClaudeFormatter` — the SDK's runtime-scoped pretty-printer.

The CLI also consumes AoD's `stage` SSE events to show provisioning progress on stderr (see [streaming.md](../../site/docs/api/streaming.md#provisioning-stages)). As each stage runs the spinner updates in place; when a stage finishes, a `✓ <label> · <duration>s` line is left in the scrollback.

## Configure

Edit the three blocks at the top of [`example-cli.py`](./example-cli.py):

| Block | What it controls |
|---|---|
| `AGENT` | Name, model, runtime, system prompt. |
| `ENVIRONMENT` | Packages installed in the Sprite, networking policy. |
| `REPOS` | GitHub repos cloned into `/workspace/<repo>` for every session. |

Fork the file per team or per workflow — one binary, one alias.

## How "ensure they exist" works

On every **new-session** run the CLI:

1. `client.environments.list()` — if a non-archived environment with the
   configured name exists, reuse its id; otherwise `client.environments.create(...)`.
2. Same for the agent via `client.agents.list()` / `client.agents.create(...)`.
3. `client.sessions.create(...)` with `agent_id`, the prompt, and the repo
   list, then streams `client.sessions.stream(session_id)` to stdout.

When `--session <id>` is passed, the ensure steps are skipped and the CLI
calls `client.sessions.prompt(id, prompt=..., timeout=...)` directly to queue
another turn against the same Sprite (same filesystem, same runtime history).
The session must be in a `pending` or `completed` state — `running`,
`failed`, and `terminated` all raise `ConflictError` (HTTP 409).

The CLI does **not** reconcile drift. If the named agent already exists with
a different model or system prompt, it's reused as-is. To roll out a config
change, either bump the name (`example-cli` → `example-cli-v2`) or archive
the old one via `POST /agents/{id}/archive`.

## Exit code

The CLI exits with the runtime's exit code — `0` on clean completion, the
runtime's non-zero code on agent failure, `1` on stream-level error/terminate.
Safe to chain: `./example-cli.py "fix the lint" && git diff`.

## Prerequisites on the Agent on Demand side

- A valid API token (`AOD_API_TOKEN`, `aod_...`).
- A runtime API key configured on your user for the agent's runtime —
  without it, session creation fails with `400 "No API key configured for
  runtime: claude"`.
- Sprites credentials configured on your user — otherwise session creation
  fails with `400 "No Sprites API key configured"`.

## Optional environment variables

- `GITHUB_TOKEN` — if set, passed through to every repo in `REPOS` as the
  clone credential. Required for private repos; leave unset for public ones.

## What's in the agent

- **Model + runtime**: `claude-sonnet-4-6` via the `claude` runtime.
- **MCP servers**: [Context7](https://context7.com) (`https://mcp.context7.com/mcp`)
  for up-to-date library docs. Add more entries to the `mcp_servers` list in
  the `AGENT` block.
