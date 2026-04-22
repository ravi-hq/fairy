# Runtimes

A **runtime** is the CLI that Agent on Demand invokes inside the Sprite to drive the model. It's a required field on an agent and determines how the session process is launched, which API key env var is read, and how multi-turn conversations are resumed.

Three runtimes are supported today:

| Runtime  | Vendor CLI       | Models                                                       | API key env var       |
| -------- | ---------------- | ------------------------------------------------------------ | --------------------- |
| `claude` | Claude Code      | `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5` (+ older `claude-opus-4-0-20250514`, `claude-sonnet-4-0-20250514`, `claude-sonnet-4-5-20250514`, `claude-3-5-haiku-20241022`) | `ANTHROPIC_API_KEY`   |
| `codex`  | OpenAI Codex CLI | `gpt-4.1`, `o3`, `o4-mini`                                   | `CODEX_API_KEY`       |
| `gemini` | Gemini CLI       | `gemini-2.5-pro`, `gemini-2.5-flash`                         | `GEMINI_API_KEY`      |

The canonical list lives in [`src/agent_on_demand/runtimes.py`](https://github.com/ravi-hq/agent-on-demand/blob/main/src/agent_on_demand/runtimes.py) (`RUNTIMES` for the runtime table, `AgentModel` for the model enum).

## Setting the runtime on an agent

Pass `runtime` and `model` when creating the agent:

```bash
curl -X POST https://aod.example.com/agents \
  -H "Authorization: Bearer $AOD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello",
    "runtime": "claude",
    "model": "claude-sonnet-4-6"
  }'
```

`runtime` must be one of the values above (400 otherwise). `model` must match a known `AgentModel` value. The two fields are validated independently — there's no server-side enforcement that a given `model` "belongs to" a given `runtime`, so it's on you to pair them sensibly.

## Supplying API keys

Each runtime reads its API key from a specific env var at session start. You set that env var on the session's **environment**, not the agent, since credentials are per-deployment rather than per-template:

```bash
curl -X POST https://aod.example.com/environments \
  -H "Authorization: Bearer $AOD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "with-anthropic-key",
    "env_vars": {"ANTHROPIC_API_KEY": "sk-ant-..."}
  }'
```

`env_vars` are encrypted at rest and never echoed back in API responses. See [Core Concepts → Environments](concepts.md#environments) for the full shape.

If no environment is attached to a session, or the environment doesn't set the runtime's expected env var, the CLI will fail on startup and the session will transition to `failed`.

## Per-runtime notes

### `claude`

Uses the Claude Code CLI in `--print` + `stream-json` mode. AoD pre-generates a UUID at session create and passes it as `--session-id` on the first turn, then `--resume <uuid>` on every subsequent turn — more reliable than `--continue` in non-interactive mode, which has been observed to silently fork new sessions.

#### `claude-oauth` (auth variant)

`claude-oauth` is the same `claude` CLI with the same command flags, but it authenticates with a Claude Pro/Max OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`) instead of an API key (`ANTHROPIC_API_KEY`). Use it when you want to run sessions against a subscription seat rather than pay-per-token API billing.

Everything else — supported models, resume semantics, output format — is identical to `claude`.

### `codex`

Uses `codex exec` with `--dangerously-bypass-approvals-and-sandbox --json`. The prompt is piped in on stdin for the first turn; subsequent turns use `codex exec resume --last` to continue in-place.

### `gemini`

Uses the Gemini CLI with `--output-format stream-json`. Resume is handled via `--resume`.

## Tools

All runtimes run with their vendor CLI's **full default tool set** — `bash`, `read`, `write`, `edit`, `glob`, `grep`, `web_fetch`, `web_search`, and so on. There is no per-agent allowlist for built-in tools, and no way to disable a specific built-in. Any MCP servers you configure on the agent are exposed to the runtime on top of the default tools.

This is intentional: Sprites are disposable sandboxes, so the tool surface is bounded by the Sprite itself rather than by a runtime-level policy.

## Streaming output shape

Every runtime emits a `start` event with `runtime` set to the runtime name, followed by the runtime's native streaming format wrapped in `output` events, then an `exit` event with the process exit code. Clients that want to render rich output will need runtime-specific parsing of the `output.data` payloads — see [Streaming](streaming.md) for the event envelope.
