---
name: aod-sdk-typescript
description: Use when writing TypeScript or JavaScript that calls the Agent on Demand API via `@ravi-hq/aod-sdk` — `new Client({...})`, `client.agents`/`environments`/`sessions`, `client.sessions.stream(...)`. Covers install, `AOD_API_URL`/`AOD_API_TOKEN` env fallbacks (Node only), the single async client (no sync variant), the `StreamHandle` async iterable with `.close()`, `AbortSignal` cancellation, typed `AodHTTPError` subclasses (`ConflictError`/`ValidationError`/`RateLimitError.limit`/`active`), and Node-vs-browser differences. Defers to the `agent-on-demand-api` skill for HTTP semantics, status codes, and state-machine edges.
---

# Agent on Demand TypeScript SDK Skill

The `@ravi-hq/aod-sdk` package wraps every endpoint in `docs/openapi.yaml` with typed interfaces, a single async `Client`, and an `AsyncIterable` SSE event stream. Zero runtime dependencies — uses built-in `fetch` / `ReadableStream` / `AbortController`. Node 18+ and modern browsers. Package source lives at `clients/typescript/` in this repo.

## When This Skill Applies

Use this skill when:
- Writing TS/JS that calls the AoD API via `import { Client } from "@ravi-hq/aod-sdk"`
- Extending `clients/typescript/` itself (new resources, new types, new stream helpers)
- Debugging a thrown `AodHTTPError` (or `ConflictError` / `ValidationError` / `RateLimitError`)

For HTTP-level questions (route table, state machine, 409/422/429 semantics), defer to the `agent-on-demand-api` skill. For Python, use `aod-sdk-python`.

## Install & Configure

```bash
npm install @ravi-hq/aod-sdk
# or, in-tree development:
cd clients/typescript && npm install && npm test
```

`new Client({...})` resolves config in this order:
1. Constructor options: `baseUrl`, `token`
2. Env vars: `AOD_API_URL`, `AOD_API_TOKEN` — **Node only**. In the browser they're undefined.
3. `baseUrl` default: `http://localhost:8777`. `token` is **required** — missing throws immediately (before any network call).

`Client` is not a `Disposable` — it holds no open connections between requests, so you don't need to close it. Streams are the exception (see below).

```ts
import { Client } from "@ravi-hq/aod-sdk";

const client = new Client({ baseUrl: "https://aod.example", token: "aod_..." });
// or just: new Client()  — reads both from env in Node
```

Other constructor options:

| Option       | Default            | Notes                                                          |
| ------------ | ------------------ | -------------------------------------------------------------- |
| `fetch`      | `globalThis.fetch` | Inject for tests/proxies. Must match the standard `fetch` shape. |
| `timeoutMs`  | `30000`            | Per non-streaming request. Streams use the caller's `AbortSignal` instead. |

## Resources Shape

Single async `Client`, three resource namespaces. Every non-stream method accepts an optional trailing `{ signal }` for `AbortSignal` cancellation.

| Namespace             | Methods                                                                                                                         |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `client.agents`       | `list()`, `create(params)`, `get(id)`, `update(id, params)`, `archive(id)`, `versions(id)`                                       |
| `client.environments` | `list()`, `create(params)`, `get(id)`, `update(id, params)`, `archive(id)`, `delete(id)`, `versions(id)`                         |
| `client.sessions`     | `list()`, `create(params)`, `get(id)`, `prompt(id, params)`, `turns(id)`, `terminate(id)`, `delete(id)`, `stream(id, options?)` |

- Return types are interfaces exported from `@ravi-hq/aod-sdk`: `Agent`, `Environment`, `Session`, `SessionAck`, `SessionTurn`, `AgentVersion`, `EnvironmentVersion`, plus param interfaces (`AgentCreateParams`, `SessionCreateParams`, etc.). Interfaces are **structural** — extra server fields don't break consumers; missing ones are typed `undefined | null`.
- IDs are plain `string`s (UUIDs on the wire). No helper type.
- `sessions.create` / `sessions.prompt` / `sessions.terminate` return `SessionAck`, **not** `Session`. Only `id` + `status` are guaranteed; `stream_url` / `environment_id` / `resources` / `current_turn` are populated when the server provides them. Call `client.sessions.get(id)` for the full record.
- Request params are `stripUndefined`d before being serialized — omitted fields aren't sent, preserving server-side defaults.

## Streaming

`client.sessions.stream(sessionId, options?)` returns a **`StreamHandle`** — an `AsyncIterable<StreamEvent>` with a `close()` method. **Always wrap in try/finally** so the underlying connection closes if you break early.

```ts
const stream = await client.sessions.stream(ack.id);
try {
  for await (const event of stream) {
    if (event.type === "output") process.stdout.write(event.extra.data as string);
    if (event.type === "exit" || event.type === "error"
        || event.type === "terminated" || event.type === "stale") break;
  }
} finally {
  await stream.close();
}
```

`StreamEvent` shape: `{ type: StreamEventType; id: number | null; extra: Record<string, unknown> }`. Everything except `type` and `id` lands in `extra`. Event schema is still evolving server-side; the SDK deliberately keeps the raw payload accessible instead of typing every field.

Event types: `start`, `turn_start`, `output`, `stage`, `exit`, `error`, `terminated`, `stale`. Terminal types are `exit`/`error`/`terminated`/`stale`.

Resume after disconnect with `{ since: lastEventId }`. Cancel with an external `AbortSignal`:

```ts
const controller = new AbortController();
const stream = await client.sessions.stream(sid, { signal: controller.signal });
// ...later:
controller.abort();          // OR
await stream.close();        // either works — both abort the underlying connection
```

The stream's internal `AbortController` is linked to whatever signal you pass — aborting the external signal aborts the stream, and `stream.close()` aborts both.

## Errors

All non-2xx responses **throw** a typed subclass of `AodError`:

| Status | Class             | Common trigger                                                |
| ------ | ----------------- | ------------------------------------------------------------- |
| 401/3  | `AuthError`       | Missing/invalid token                                         |
| 404    | `NotFoundError`   | Resource missing or not owned by the token's user             |
| 409    | `ConflictError`   | Archive-already, terminal session, stale `version`, failed-resume |
| 422    | `ValidationError` | Pydantic validation on the server — `detail` is a **list**    |
| 429    | `RateLimitError`  | Concurrent-session quota. Has `.limit` and `.active` props (may be `null`) |
| 5xx    | `ServerError`     | Sprites upstream error or unhandled exception                 |

All share `.statusCode`, `.detail`, `.method`, `.url`. `detail` is `unknown` in the TS types — it's a `string` for most codes and an **array of error objects** for 422. Narrow with `instanceof` rather than branching on `statusCode`, and on 422 check `Array.isArray(err.detail)` before reading.

```ts
import { ConflictError, RateLimitError } from "@ravi-hq/aod-sdk";

try {
  await client.agents.update(agent.id, { version: agent.version, name: "renamed" });
} catch (e) {
  if (e instanceof ConflictError) {
    const latest = await client.agents.get(agent.id);
    await client.agents.update(latest.id, { version: latest.version, name: "renamed" });
  } else if (e instanceof RateLimitError) {
    console.warn(`quota: ${e.active}/${e.limit}`);
  } else {
    throw e;
  }
}
```

## Optimistic Concurrency Idiom

Agents and environments require `version` on every update. Stale → `ConflictError`.

```ts
const agent = await client.agents.get(agentId);
await client.agents.update(agent.id, { version: agent.version, system: "..." });
```

Merge/replace semantics match the HTTP API (see `agent-on-demand-api`):
- `metadata` is **merged per-key**; empty string deletes the key.
- `env_vars` is **fully replaced** — re-send every key you want to keep.

## Node vs Browser

The SDK is isomorphic with a few caveats:

- **Env var fallbacks are Node-only.** In a browser pass `baseUrl` and `token` explicitly.
- **CORS**: calling the AoD API from a browser on a different origin requires the server to send CORS headers. The SDK itself does nothing special.
- **No `process.env` leaks**: config resolution guards `typeof process !== "undefined"` so bundlers/Deno/browsers don't choke.
- **Dependencies: zero.** If you see anything imported outside the `@ravi-hq/aod-sdk` tree, it's a bug.

## Common Gotchas

1. **Missing token throws synchronously from `new Client({})`.** No network roundtrip to discover the config is broken.
2. **`Session` has no `prompt`.** `prompt` lives on `SessionTurn` — fetch with `client.sessions.turns(sessionId)`.
3. **`SessionAck.environment_id` is absent on `prompt` / `terminate` acks.** Only populated on `create`. Don't rely on it after a resume.
4. **`for await` of a `StreamHandle` leaks the connection if you don't `await stream.close()` in `finally`.** The `close()` aborts the fetch; without it the reader can outlive your function.
5. **`event.extra.data` (for `output`) is typed `unknown`.** You know from the server that it's a `string`, but the SDK types it `unknown` because the payload shape is evolving. Cast at the boundary.
6. **There's no sync client.** Node's `fetch` is async — use top-level `await` in modules or wrap in an `async` function.
7. **`VERSION` exported from the package is a plain `const`** (`"0.1.1"` at time of writing). Keep it in sync with `package.json` when releasing (release script enforces this).

## End-to-End Example

```ts
import { Client } from "@ravi-hq/aod-sdk";

const client = new Client({ token: "aod_..." });

const env = await client.environments.create({
  name: "demo",
  packages: { pip: ["requests"] },
  env_vars: { DEMO: "1" },
  networking: { type: "limited", allowed_hosts: ["pypi.org"] },
});

const agent = await client.agents.create({
  name: "demo",
  model: "anthropic/claude-sonnet-4-6",
  runtime: "claude",
  system: "You are terse.",
  environment_id: env.id,
});

const ack = await client.sessions.create({
  agent_id: agent.id,
  prompt: "summarize README.md",
  resources: [{ type: "github_repository", url: "https://github.com/me/repo" }],
});

const stream = await client.sessions.stream(ack.id);
try {
  for await (const event of stream) {
    if (event.type === "output") process.stdout.write(event.extra.data as string);
    if (["exit", "error", "terminated", "stale"].includes(event.type)) break;
  }
} finally {
  await stream.close();
}

const final = await client.sessions.get(ack.id);
console.log(`status=${final.status} exit_code=${final.exit_code}`);
```

## Related Files

- `clients/typescript/src/client.ts` — `Client` + config resolution
- `clients/typescript/src/resources/` — `agents.ts`, `environments.ts`, `sessions.ts`, barrel `index.ts`
- `clients/typescript/src/types.ts` — interfaces (`Agent`, `Session`, `StreamEvent`, ...) and `streamEventFromPayload`
- `clients/typescript/src/errors.ts` — error classes + `raiseForStatus`
- `clients/typescript/src/stream.ts` — `createStreamHandle` + SSE reader
- `clients/typescript/src/index.ts` — public exports barrel
- `clients/typescript/README.md` — user-facing docs
- Sibling skill `agent-on-demand-api` — HTTP semantics, status codes, state machine
