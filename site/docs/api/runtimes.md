# Runtimes

A **runtime** is the CLI that Agent on Demand invokes inside the Sprite to drive the model.
It is a required field on an agent and determines how the session process is launched,
which API key env var is read, and how multi-turn conversations are resumed.

Four runtimes are supported:

| Runtime    | Vendor CLI       | Providers                       | API key env var(s)                                   |
| ---------- | ---------------- | ------------------------------- | ----------------------------------------------------- |
| `claude`   | Claude Code      | `anthropic`                     | `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN` — see below) |
| `codex`    | OpenAI Codex CLI | `openai`                        | `OPENAI_API_KEY`                                     |
| `gemini`   | Gemini CLI       | `google`                        | `GEMINI_API_KEY`                                     |
| `opencode` | opencode         | `anthropic`, `openai`, `google` | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` |

Model strings use the canonical `provider/model_id` form. The full model catalog lives in
[`src/agent_on_demand/models_catalog.py`](https://github.com/ravi-hq/agent-on-demand/blob/main/src/agent_on_demand/models_catalog.py).

Runtime source: [`src/agent_on_demand/runtimes/`](https://github.com/ravi-hq/agent-on-demand/blob/main/src/agent_on_demand/runtimes/).

## Setting the runtime on an agent

Pass `runtime` and `model` when creating the agent:

```bash
curl -X POST https://aod.ravi.id/agents \
  -H "Authorization: Bearer $AOD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello",
    "runtime": "claude",
    "model": "anthropic/claude-sonnet-4-6"
  }'
```

`runtime` must be one of the values in the table above (400 otherwise). `model` must be a
key in the model catalog. On create and update, the server validates that the runtime's
`providers` set includes the model's provider — mismatches return 422.

## Supplying API keys

Each runtime reads its API key from a specific env var at session start. On the hosted API
(`aod.ravi.id`) you register credentials once via the dashboard; they are stored encrypted
and injected into every session you own.

When self-hosting, set the env var on the session's **environment**:

```bash
curl -X POST https://aod.ravi.id/environments \
  -H "Authorization: Bearer $AOD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "with-anthropic-key",
    "env_vars": {"ANTHROPIC_API_KEY": "sk-ant-..."}
  }'
```

`env_vars` are encrypted at rest and never echoed back in API responses.
See [Core Concepts → Environments](concepts.md#environments) for the full shape.

If no environment is attached to a session, or the environment doesn't set the runtime's
expected env var, the CLI will fail on startup and the session will transition to `failed`.

## Per-runtime notes

### `claude`

Uses the Claude Code CLI in `--print` + `stream-json` mode. AoD pre-generates a UUID at
session create and passes it as `--session-id` on the first turn, then `--resume <uuid>`
on every subsequent turn — more reliable than `--continue` in non-interactive mode, which
has been observed to silently fork new sessions.

**OAuth auth variant:** if you register a `runtime_token:claude-oauth` credential (a Claude
Pro/Max OAuth token), it is written as `CLAUDE_CODE_OAUTH_TOKEN` to the session Sprite
instead of `ANTHROPIC_API_KEY`. The CLI picks it up automatically. Everything else —
supported models, resume semantics, output format — is identical. Use it when you want to
run sessions against a subscription seat rather than pay-per-token API billing.

### `codex`

Uses `codex exec` with `--dangerously-bypass-approvals-and-sandbox --json`. The prompt
is piped in on stdin for the first turn; subsequent turns use `codex exec resume --last`
to continue in-place.

### `gemini`

Uses the Gemini CLI with `--output-format stream-json`. Resume is handled via `--resume`.

### `opencode`

A multi-provider meta-runtime: one `opencode` CLI fronts `anthropic`, `openai`, and
`google` providers, selecting provider and model per invocation via `--model provider/model_id`.

**Installation:** opencode is not pre-installed on the Sprite base image. Sessions install
it via `npm i -g opencode-ai` during the `provision_setup` stage (before any network
policy is applied, so `registry.npmjs.org` does not need to be in `allowed_hosts`).
First-session provisioning takes ~10–30 s longer than the pre-baked runtimes.

## Tools

All runtimes run with their vendor CLI's **full default tool set** — `bash`, `read`,
`write`, `edit`, `glob`, `grep`, `web_fetch`, `web_search`, and so on. There is no
per-agent allowlist for built-in tools, and no way to disable a specific built-in.
Any MCP servers you configure on the agent are exposed to the runtime on top of the
default tools.

This is intentional: Sprites are disposable sandboxes, so the tool surface is bounded
by the Sprite itself rather than by a runtime-level policy.

## Streaming output shape

Every runtime emits a `start` event with `runtime` set to the runtime name, followed by
the runtime's native streaming format wrapped in `output` events, then an `exit` event
with the process exit code. See [Streaming](streaming.md) for the full event envelope.
