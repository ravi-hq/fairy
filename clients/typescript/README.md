# @ravi-hq/aod-sdk

TypeScript SDK for the [Agent on Demand](../../README.md) HTTP API. Covers every
endpoint in [`docs/openapi.yaml`](../../docs/openapi.yaml) with typed models,
a single async `Client`, and an `AsyncIterable` SSE event stream. Works in
Node 18+ and modern browsers with zero runtime dependencies.

## Install

```bash
npm install @ravi-hq/aod-sdk
```

## Quickstart

```ts
import { Client } from "@ravi-hq/aod-sdk";

const client = new Client({
  baseUrl: "https://aod.example",
  token: "aod_...",
});

const env = await client.environments.create({
  name: "prod",
  packages: { apt: ["jq"], npm: ["typescript"] },
  env_vars: { OPENAI_API_KEY: "sk-..." },
  networking: { type: "limited", allowed_hosts: ["api.github.com"] },
});

const agent = await client.agents.create({
  name: "my-agent",
  model: "claude-sonnet-4-5",
  runtime: "claude-code",
  system: "You are a careful software engineer.",
  environment_id: env.id,
});

const ack = await client.sessions.create({
  agent_id: agent.id,
  prompt: "implement the feature in TODO.md",
  resources: [
    { type: "github_repository", url: "https://github.com/me/repo" },
  ],
});

const stream = await client.sessions.stream(ack.id);
try {
  for await (const event of stream) {
    console.log(event.type, event.extra);
  }
} finally {
  await stream.close();
}
```

## Configuration

`baseUrl` and `token` can be passed to the constructor or read from
`AOD_API_URL` and `AOD_API_TOKEN` environment variables (Node only).
`baseUrl` defaults to `http://localhost:8777`.

| Option     | Default                | Notes                                              |
| ---------- | ---------------------- | -------------------------------------------------- |
| `baseUrl`  | `AOD_API_URL` or localhost | Trailing slash stripped.                        |
| `token`    | `AOD_API_TOKEN`        | Required. Sent as `Authorization: Bearer <token>`. |
| `fetch`    | `globalThis.fetch`     | Inject a custom fetch (tests, proxies).            |
| `timeoutMs`| 30000                  | Per-request timeout. Streaming requests pass a signal instead. |

## Errors

Non-2xx responses throw a typed subclass of `AodHTTPError`:

| Status | Class              | When                                                         |
| ------ | ------------------ | ------------------------------------------------------------ |
| 401/3  | `AuthError`        | Missing/invalid token                                        |
| 404    | `NotFoundError`    | Resource missing                                             |
| 409    | `ConflictError`    | Archived row, terminal session, or stale `version`           |
| 422    | `ValidationError`  | Server-side validation failure                               |
| 429    | `RateLimitError`   | Per-user concurrent session limit (`.limit`, `.active`)      |
| 5xx    | `ServerError`      |                                                              |

All share `.statusCode`, `.detail`, `.method`, `.url`.

## Optimistic concurrency

`agents` and `environments` require the current `version` on update. A stale
version throws `ConflictError`:

```ts
const agent = await client.agents.get(agentId);
await client.agents.update(agent.id, { version: agent.version, name: "renamed" });
```

## Streaming

`client.sessions.stream(sessionId, { since, signal })` returns a `StreamHandle`
— an `AsyncIterable<StreamEvent>` with a `close()` method.

```ts
const stream = await client.sessions.stream(sessionId, { since: lastSeen });
try {
  for await (const event of stream) {
    if (event.type === "exit") break;
  }
} finally {
  await stream.close();
}
```

Event types: `start`, `turn_start`, `output`, `stage`, `exit`, `error`,
`terminated`, `stale`. Everything except `type` and `id` lands in `event.extra`
— the event schema is still evolving server-side and the SDK keeps the raw
payload accessible.

Cancellation works via an external `AbortSignal` passed in `opts.signal`, or by
calling `stream.close()`.

## Browser use

The SDK has no Node-only dependencies — it uses built-in `fetch`,
`ReadableStream`, and `AbortController`. A few notes:

- `AOD_API_URL` / `AOD_API_TOKEN` env fallbacks only apply in Node. In the
  browser pass `baseUrl` and `token` explicitly.
- If you're calling the AoD API from a browser origin that isn't the API's
  own origin, the server must send the appropriate CORS headers.

## Development

```bash
cd clients/typescript
npm install
npm test
npm run typecheck
npm run build
```

## Releases (maintainers)

Published to npm via GitHub Actions using npm provenance + an npm automation
token stored as the `NPM_TOKEN` repository secret. The workflow lives at
`.github/workflows/sdk-release-npm.yml` and fires on a GitHub Release tagged
`aod-sdk-ts-v<version>`.

### One-time setup

1. Reserve `@ravi-hq/aod-sdk` on [npm](https://www.npmjs.com/) (the `ravi-hq`
   org must exist and your account must be a member).
2. Create an npm automation token with publish access and add it as
   `NPM_TOKEN` in the repo's Actions secrets.
3. (Recommended) Create a protected GitHub environment `npm` with required
   reviewers.

### Cutting a release

1. Bump the version in `clients/typescript/package.json` **and** the `VERSION`
   export in `clients/typescript/src/index.ts`. PR + merge to `main`.
2. Tag and publish a GitHub Release:

   ```bash
   gh release create aod-sdk-ts-v0.1.0 \
     --title "aod-sdk (TypeScript) v0.1.0" \
     --notes "..." \
     --target main
   ```

   `sdk-release-npm.yml` fires on `published`, verifies the tag matches
   `package.json`, runs tests, builds, and publishes with provenance.
