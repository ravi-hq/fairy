# Runtimes

A **runtime** is the CLI that Agent on Demand invokes inside the Sprite to drive the model. It's a required field on an agent and determines how the session process is launched, which API key env var is read, and how multi-turn conversations are resumed.

Four runtimes are supported:

| Runtime    | Vendor CLI         | Models (canonical `provider/model_id`)                                                                                                  | API key env var                        |
| ---------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `claude`   | Claude Code        | `anthropic/claude-opus-4-6`, `anthropic/claude-sonnet-4-6`, `anthropic/claude-haiku-4-5` (+ older dated variants)                       | `ANTHROPIC_API_KEY`                    |
| `codex`    | OpenAI Codex CLI   | `openai/gpt-4.1`, `openai/o3`, `openai/o4-mini`                                                                                         | `OPENAI_API_KEY`                       |
| `gemini`   | Gemini CLI         | `google/gemini-2.5-pro`, `google/gemini-2.5-flash`                                                                                      | `GEMINI_API_KEY`                       |
| `opencode` | opencode (sst/opencode) | Any `anthropic/*`, `openai/*`, or `google/*` model in the catalog                                                                 | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` |

Model strings are in canonical `provider/model_id` form — e.g. `anthropic/claude-sonnet-4-6`, not `claude-sonnet-4-6`. The full list lives in [`src/agent_on_demand/models_catalog.py`](https://github.com/ravi-hq/agent-on-demand/blob/main/src/agent_on_demand/models_catalog.py); the runtime registry is in [`src/agent_on_demand/runtimes/__init__.py`](https://github.com/ravi-hq/agent-on-demand/blob/main/src/agent_on_demand/runtimes/__init__.py).

## Setting the runtime on an agent

Pass `runtime` and `model` when creating the agent:

```bash
curl -X POST https://aod.example.com/agents \
  -H "Authorization: Bearer $AOD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello",
    "runtime": "claude",
    "model": "anthropic/claude-sonnet-4-6"
  }'
```

`runtime` must be one of the four values above (400 otherwise). `model` must be a known canonical model ID. The server validates them independently — pairing a mismatched runtime and model (e.g. an OpenAI model with the `claude` runtime) will pass validation but fail at session execution when the CLI rejects the model name.

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

Uses the Claude Code CLI in `--print` + `stream-json` mode. AoD pre-generates a UUID at session create and passes it as `--session-id` on the first turn, then `--resume <uuid>` on every subsequent turn — more reliable than `--continue` in non-interactive mode.

#### OAuth auth variant

The `claude` runtime also supports Claude Pro/Max OAuth tokens. Register a `runtime_token:claude-oauth` credential for a user and AoD will export `CLAUDE_CODE_OAUTH_TOKEN` instead of `ANTHROPIC_API_KEY`. Everything else — models, resume semantics, output format — is identical. The runtime string on the agent remains `"claude"`.

### `codex`

Uses `codex exec` with `--dangerously-bypass-approvals-and-sandbox --json`. The prompt is piped in on stdin for the first turn; subsequent turns use `codex exec resume --last` to continue in-place.

### `gemini`

Uses the Gemini CLI with `--output-format stream-json`. Resume is handled via `--resume`.

### `opencode`

Uses [sst/opencode](https://opencode.ai) — a multi-provider CLI that fronts Anthropic, OpenAI, and Google models through a single binary. Pass any `anthropic/*`, `openai/*`, or `google/*` model ID; opencode picks the right provider API at invocation time.

opencode is **not pre-installed** on the Sprite base image. AoD runs `npm install -g opencode-ai@<pinned>` during session provisioning (before any network policy is applied, so `registry.npmjs.org` does not need to be in `allowed_hosts`). First-session provisioning takes 10–30 s longer than the pre-baked runtimes as a result.

If the environment has `networking.type == "limited"`, the allowed-hosts list must include `registry.npmjs.org` for opencode provisioning to succeed.

## Tools

All runtimes run with their vendor CLI's **full default tool set** — `bash`, `read`, `write`, `edit`, `glob`, `grep`, `web_fetch`, `web_search`, and so on. There is no per-agent allowlist for built-in tools, and no way to disable a specific built-in. Any MCP servers you configure on the agent are exposed to the runtime on top of the default tools.

This is intentional: Sprites are disposable sandboxes, so the tool surface is bounded by the Sprite itself rather than by a runtime-level policy.

## Streaming output shape

Every runtime emits a `start` event with `runtime` set to the runtime name, followed by the runtime's native streaming format wrapped in `output` events, then an `exit` event with the process exit code. Clients that want to render rich output will need runtime-specific parsing of the `output.data` payloads — see [Streaming](streaming.md) for the event envelope.
