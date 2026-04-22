# example-cli

A minimal Python CLI (stdlib only, no pip install) that runs one prompt
through Agent on Demand and streams the output. Meant to replace team aliases
like `claude --dangerously-skip-permissions -p "<prompt>"` with a version that
runs inside a Sprite sandbox against a pinned agent, tool set, and repo list.

```
$ export AOD_URL=https://aod.example.com
$ export AOD_TOKEN=aod_xxxxxxxx
$ ./example-cli "work on the latest open issue in ravi-hq/fairy"
# session 8f3a...
Let me look at the issue list...
```

## Configure

Edit the three blocks at the top of [`example-cli`](./example-cli):

| Block | What it controls |
|---|---|
| `AGENT` | Name, model, runtime, system prompt. |
| `ENVIRONMENT` | Packages installed in the Sprite, networking policy. |
| `REPOS` | GitHub repos cloned into `/workspace/<repo>` for every session. |

Fork the file per team or per workflow — one binary, one alias.

## How "ensure they exist" works

On every run the CLI:

1. `GET /environments` — if a non-archived environment with the configured
   name exists, reuse its id; otherwise `POST /environments` to create it.
2. Same for the agent via `GET /agents` / `POST /agents`.
3. `POST /sessions` with `agent_id`, the prompt, and the repo list, then
   streams `GET /sessions/{id}/stream` to stdout.

The CLI does **not** reconcile drift. If the named agent already exists with
a different model or system prompt, it's reused as-is. To roll out a config
change, either bump the name (`example-cli` → `example-cli-v2`) or archive
the old one via `POST /agents/{id}/archive`.

## Exit code

The CLI exits with the runtime's exit code — `0` on clean completion, the
runtime's non-zero code on agent failure, `1` on stream-level error/terminate.
Safe to chain: `./example-cli "fix the lint" && git diff`.

## Prerequisites on the Agent on Demand side

- A valid API token (`AOD_TOKEN`, `aod_...`).
- A runtime API key configured on your user for the agent's runtime —
  without it, `POST /sessions` returns `400 "No API key configured for
  runtime: claude"`.
- Sprites credentials configured on your user — otherwise `POST /sessions`
  returns `400 "No Sprites API key configured"`.
