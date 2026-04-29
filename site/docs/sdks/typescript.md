# TypeScript SDK

The official TypeScript client for Agent on Demand. Covers every endpoint with typed models, a single async `Client`, and an `AsyncIterable` SSE event stream. Works in Node 18+ and modern browsers with zero runtime dependencies.

- **Package**: [`@ravi-hq/aod-sdk` on npm](https://www.npmjs.com/package/@ravi-hq/aod-sdk)
- **Source**: [`clients/typescript/`](https://github.com/ravi-hq/agent-on-demand/tree/main/clients/typescript)
- **Supports**: Node 18+, modern browsers

## Install

```bash
npm install @ravi-hq/aod-sdk
```

## Quickstart

```ts
import { Client } from "@ravi-hq/aod-sdk";

const client = new Client({
  baseUrl: "http://localhost:8777",
  token: "aod_...",
  // or set AOD_API_URL / AOD_API_TOKEN in env (Node only)
});

const agent = await client.agents.create({
  name: "hello",
  model: "anthropic/claude-sonnet-4-6",
  runtime: "claude",
});

const ack = await client.sessions.create({
  agent_id: agent.id,
  prompt: "Say hello.",
  timeout: 120,
});

const stream = await client.sessions.stream(ack.id);
try {
  for await (const event of stream) {
    if (event.type === "output") {
      process.stdout.write(event.extra.data ?? "");
    } else if (event.type === "exit") {
      console.log(`\n[exit ${event.extra.code}]`);
      break;
    }
  }
} finally {
  await stream.close();
}
```

## Configuration

`baseUrl` and `token` can be passed to the constructor or read from `AOD_API_URL` and `AOD_API_TOKEN` environment variables (Node only). `baseUrl` defaults to `http://localhost:8777`.

| Option      | Default                         | Notes                                                      |
| ----------- | ------------------------------- | ---------------------------------------------------------- |
| `baseUrl`   | `AOD_API_URL` or `localhost:8777` | Trailing slash stripped.                                |
| `token`     | `AOD_API_TOKEN`                 | Required. Sent as `Authorization: Bearer <token>`.         |
| `fetch`     | `globalThis.fetch`              | Inject a custom fetch for tests or proxies.                |
| `timeoutMs` | `30000`                         | Per-request timeout. Streaming requests use `AbortSignal`. |

## Feature summary

| Capability | Where it lives |
|------------|----------------|
| Typed resources | `client.agents`, `client.environments`, `client.sessions` |
| Typed models | `Agent`, `Environment`, `Session`, `SessionAck`, `SessionTurn`, `StreamEvent`, … |
| Typed error hierarchy | `AodHTTPError` → `NotFoundError`, `ConflictError`, `ValidationError`, `RateLimitError`, `AuthError`, `ServerError` |
| Multi-turn | `client.sessions.prompt(sessionId, { prompt })` → `SessionAck` |
| Session teardown | `client.sessions.terminate(sessionId)`, `client.sessions.delete(sessionId)` |
| Turn history | `client.sessions.turns(sessionId)` → `SessionTurn[]` |
| SSE stream (`AsyncIterable`, `close()`) | `client.sessions.stream(sessionId, { since?, signal? })` |
| Version history | `client.agents.versions(agentId)` / `client.environments.versions(environmentId)` |

## Errors

Non-2xx responses throw a typed subclass of `AodHTTPError`. All share `.statusCode`, `.detail`, `.method`, `.url`:

```ts
import { ConflictError } from "@ravi-hq/aod-sdk";

try {
  await client.agents.update(agentId, { version: 1, name: "renamed" });
} catch (err) {
  if (err instanceof ConflictError) {
    // 409: stale version or archived row
    console.error(err.statusCode, err.detail);
  }
}
```

| Status | Class            | When                                                     |
| ------ | ---------------- | -------------------------------------------------------- |
| 401    | `AuthError`      | Missing or invalid token                                 |
| 404    | `NotFoundError`  | Resource missing                                         |
| 409    | `ConflictError`  | Archived row, terminal session, or stale `version`       |
| 422    | `ValidationError`| Server-side validation failure                           |
| 429    | `RateLimitError` | Per-user concurrent session limit (`.limit`, `.active`)  |
| 5xx    | `ServerError`    |                                                          |

## Optimistic concurrency

`agents` and `environments` carry a `version` integer. Pass the current version on every `PUT`; a stale version throws `ConflictError`:

```ts
const agent = await client.agents.get(agentId);
await client.agents.update(agent.id, {
  version: agent.version,
  name: "renamed",
});
```

See [Core Concepts → Optimistic concurrency](../api/concepts.md#optimistic-concurrency-agents-and-environments) for the full semantics.

## Streaming

`client.sessions.stream(sessionId, opts?)` returns a `StreamHandle` — an `AsyncIterable<StreamEvent>` with a `close()` method.

```ts
const stream = await client.sessions.stream(sessionId, { since: lastSeenId });
try {
  for await (const event of stream) {
    switch (event.type) {
      case "stage":
        console.log(`[${event.extra.stage} ${event.extra.state}]`);
        break;
      case "output":
        process.stdout.write(event.extra.data ?? "");
        break;
      case "exit":
        console.log(`\n[exit ${event.extra.code}]`);
        return;
      case "error":
      case "terminated":
      case "stale":
        console.error(`\n[${event.type}]`);
        return;
    }
  }
} finally {
  await stream.close();
}
```

Pass `since: lastSeenId` to resume after a disconnect. Pass `signal: abortController.signal` to cancel from outside.

Event types: `start`, `turn_start`, `output`, `stage`, `exit`, `error`, `terminated`, `stale`. Everything except `type` and `id` lands in `event.extra`. See [Streaming reference](../api/streaming.md) for the full event schema.

## Multi-turn sessions

After a session reaches `completed`, call `client.sessions.prompt()` to send a follow-up. The agent resumes in the same Sprite with the same filesystem and conversation history.

```ts
import { Client, ConflictError } from "@ravi-hq/aod-sdk";
import type { SessionAck } from "@ravi-hq/aod-sdk";

const client = new Client({ token: "aod_..." });

// Turn 1
const ack = await client.sessions.create({
  agent_id: agentId,
  prompt: "List the TypeScript files here.",
});
const turn1 = ack.current_turn;
const stream1 = await client.sessions.stream(ack.id);
try {
  for await (const event of stream1) {
    if (event.type === "output" && event.extra.turn === turn1) {
      process.stdout.write((event.extra.data as string) ?? "");
    } else if (
      event.type === "exit" ||
      event.type === "error" ||
      event.type === "terminated" ||
      event.type === "stale"
    ) {
      break;
    }
  }
} finally {
  await stream1.close();
}

// Turn 2 — only valid once session is `completed`
let ack2: SessionAck;
try {
  ack2 = await client.sessions.prompt(ack.id, {
    prompt: "Now summarise what each file does.",
  });
} catch (err) {
  if (err instanceof ConflictError) {
    // session is pending, running, failed, or terminated — inspect err.detail
    // and either retry (pending/running) or start a new session (failed/terminated)
    throw err;
  } else {
    throw err;
  }
}

const turn2 = ack2.current_turn;
const stream2 = await client.sessions.stream(ack.id);
try {
  for await (const event of stream2) {
    if (event.type === "output" && event.extra.turn === turn2) {
      process.stdout.write((event.extra.data as string) ?? "");
    } else if (
      event.type === "exit" ||
      event.type === "error" ||
      event.type === "terminated" ||
      event.type === "stale"
    ) {
      break;
    }
  }
} finally {
  await stream2.close();
}
```

`prompt()` returns a `SessionAck` with the updated `current_turn`. Only `completed` sessions accept a prompt — `running`, `pending`, `failed`, and `terminated` all throw `ConflictError` (409). See [Core Concepts → Session state machine](../api/concepts.md#session-state-machine).

## Browser use

The SDK has no Node-only dependencies — it uses built-in `fetch`, `ReadableStream`, and `AbortController`. Pass `baseUrl` and `token` explicitly in browser code; the `AOD_API_URL` / `AOD_API_TOKEN` env fallbacks are Node-only.

If you're calling Agent on Demand from a browser origin other than the API's own origin, the server must send appropriate CORS headers (not enabled by default on self-hosted instances).

## See also

- [`clients/typescript/README.md`](https://github.com/ravi-hq/agent-on-demand/tree/main/clients/typescript#readme) — full API surface and release notes.
- [Python SDK](python.md) — the Python equivalent with sync and async clients.
- [Streaming reference](../api/streaming.md) — event types, heartbeats, resume.
