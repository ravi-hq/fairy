# aod-sdk improvement backlog

Tracking doc for the rolling effort to bring `clients/python` (the `aod`
package) in line with every feature exposed by the AoD HTTP API. Each
numbered item below is a PR-sized unit of work. The omnibus PR
(`sdk/omnibus`) rolls them up so a reviewer can see the cumulative
diff in one place.

## State of the SDK (snapshot)

Endpoint coverage today: complete. Every URL in
`src/agent_on_demand/urls.py` has a sync + async method on the
matching resource class:

| Endpoint                                | SDK method                          |
| --------------------------------------- | ----------------------------------- |
| `GET /health`                           | `client.health()`                   |
| `GET/POST /agents`                      | `client.agents.list/create`         |
| `GET/PUT /agents/{id}`                  | `client.agents.get/update`          |
| `POST /agents/{id}/archive`             | `client.agents.archive`             |
| `GET /agents/{id}/versions`             | `client.agents.versions`            |
| `GET/POST /environments`                | `client.environments.list/create`   |
| `GET/PUT /environments/{id}`            | `client.environments.get/update`    |
| `POST /environments/{id}/archive`       | `client.environments.archive`       |
| `DELETE /environments/{id}/delete`      | `client.environments.delete`        |
| `GET /environments/{id}/versions`       | `client.environments.versions`      |
| `GET/POST /sessions`                    | `client.sessions.list/create`       |
| `GET /sessions/{id}`                    | `client.sessions.get`               |
| `POST /sessions/{id}/prompt`            | `client.sessions.prompt`            |
| `GET /sessions/{id}/turns`              | `client.sessions.turns`             |
| `POST /sessions/{id}/terminate`         | `client.sessions.terminate`         |
| `DELETE /sessions/{id}/delete`          | `client.sessions.delete`            |
| `GET /sessions/{id}/stream`             | `client.sessions.stream`            |

So "every feature is reachable" is mostly already true — but several
features are reachable *only by passing raw dicts*. The work below
closes the remaining gaps in type fidelity, DX, and docs.

## Backlog

Ordered by impact-per-effort. Each entry is one PR.

### Type fidelity (server validation has shapes the SDK doesn't expose)

1. **Typed MCP server request models.** API accepts two distinct shapes
   (`url` / `stdio`) with different required fields
   (`mcp_server_validation.py`). SDK request methods take
   `list[dict[str, Any]]` so users get no validation until the server
   422s. Add `McpServerUrl` / `McpServerStdio` typed inputs, accept
   `dict | typed` in `agents.create/update`, validate at call time.

2. **Typed Skill request models.** API accepts inline (`name`,
   `description`, `content`) and github (`type=github`, `source`,
   optional `name`) shapes with strict field allowlists, name regex,
   content size cap, heredoc-delimiter blocklist
   (`skill_validation.py`). SDK takes `list[dict[str, Any]]`. Add
   `InlineSkill` / `GithubSkill` discriminated types; pre-validate
   client-side to fail fast on the obvious mistakes (length, name
   regex, missing fields).

3. **Typed session resource with `authorization_token`.** API supports
   `authorization_token` on `github_repository` resources (private repo
   PAT). SDK accepts a dict so users *can* pass it, but there's no
   typed surface — `resources=[{"type": ..., "authorization_token":
   "ghp_..."}]` is the only path. Add `GithubRepoResource` request
   model with `authorization_token`, accept `dict | typed`. Mirrors the
   Pydantic model already on the server.

4. **Typed networking for env create/update.** API takes
   `{"type": "unrestricted" | "limited", "allowed_hosts": [...]}`
   with regex validation (`environment_validation.py`). SDK takes
   `dict[str, Any]`. Add `NetworkingRequest` typed input.

### DX (high-value helpers that don't exist today)

5. **`sessions.run()` — create + stream + collect.** Today's golden
   path is six lines:

   ```python
   ack = client.sessions.create(...)
   with client.sessions.stream(ack.id) as events:
       for event in events:
           ...
   ```

   Add `client.sessions.run(...)` that does create + stream + return a
   final result object (status, exit_code, collected events,
   stdout/stderr if requested). Same for async. This is the single
   biggest DX win in the SDK.

6. **`sessions.wait_for_completion(session_id, timeout=...)`.** Often
   you've already created a session (or are resuming an existing one)
   and just want a blocking wait. Implement on top of the stream so it
   reuses the SSE plumbing.

7. **`Last-Event-ID` header on stream resume.** Server reads
   `HTTP_LAST_EVENT_ID` *or* `?since=`. SDK only sends `?since=`.
   Adding the header makes resume work transparently with any
   spec-compliant SSE client/proxy in the chain.

8. **Stream auto-reconnect on transient disconnect.** SSE drops happen
   (proxies, idle timeouts). Add an opt-in
   `stream(session_id, auto_reconnect=True)` that catches
   `httpx.RemoteProtocolError` / `httpx.ReadError`, sleeps with
   exponential backoff, and reconnects with `since=<last seen id>`.
   Off by default to keep the existing behavior.

9. **Retry on 5xx with backoff.** Configurable transport-level retry
   for idempotent methods (GET / DELETE /archive). Users can wire it
   today via `httpx.HTTPTransport(retries=...)` but a first-class
   knob on `Client(...)` is friendlier.

### Pretty-printers (currently only Claude has one)

10. **`aod.pretty.codex` formatter.** Codex CLI emits its own line
    format. Mirror the `ClaudeFormatter` shape (`consume(event)`,
    `feed(chunk)`, `flush()`).

11. **`aod.pretty.gemini` formatter.** Same, for Gemini CLI.

12. **`aod.pretty.opencode` formatter.** Same, for OpenCode.

### Documentation / spec sync

13. **OpenAPI sync sweep.** `docs/openapi.yaml` is stale. Diff it
    against the current view code and update:
    request shapes (skills, mcp_servers, networking, resources),
    response shapes (session ack vs session detail),
    error shapes (429 with `limit`/`active`, 422 detail-as-string),
    new endpoints/fields landed since the last sweep. May be one PR or
    split by section depending on size.

14. **README quickstart matches the typed surface.** Once 1–4 land,
    update the quickstart in `clients/python/README.md` to use typed
    inputs in at least one example, with a note that dicts still work.

## Out of scope (intentionally)

- Pagination on list endpoints — the API doesn't paginate yet; an
  iterator in the SDK without server pagination is fake polish.
- Credential management endpoints — none exist on the API. UI-only.
- A `Session` object with bound methods (`session.prompt(...)`,
  `session.stream()`) — possible later, but every method already takes
  a `session_id` so the ergonomic gap is small.

## Process

- One PR per backlog item, branched off `main` as `sdk/<slug>`.
- Each PR is independently mergeable (no stacking).
- The omnibus branch `sdk/omnibus` is force-pushed each iteration as
  `main + (all open feature branches merged in)`. Its PR description
  links each component PR. Reviewers can read the omnibus diff for the
  full picture or click through to each component PR for focused
  review.
- This file is the single source of truth for what's done, what's
  open, and what's next; tick items as the PRs land.
