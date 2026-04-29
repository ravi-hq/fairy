# Errors

## Error envelope

All error responses use a single shape:

```json
{"detail": "<string or list>"}
```

- For every status code **except 422**, `detail` is a string.
- For **422**, `detail` is a list of Pydantic validation error objects:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["prompt"],
      "msg": "Field required",
      "input": {}
    }
  ]
}
```

Client rule: `isinstance(detail, list)` if and only if status is 422.

## Status code reference

| Code | `detail` type | Meaning | Example body |
|------|--------------|---------|-------------|
| 200 | string or object | OK; also used for successful deletes/terminates | `{"detail":"Session deleted"}` |
| 201 | object | Resource created (agents, environments) | `{"id":"...","version":1,...}` |
| 202 | object | Session accepted, running in background | `{"id":"...","status":"pending","stream_url":"..."}` |
| 400 | string | Bad request: invalid JSON, unknown runtime, runtime has no configured API key | `{"detail":"Invalid JSON"}` |
| 401 | string | Missing, invalid, inactive, or expired token | `{"detail":"Invalid API key"}` |
| 404 | string | Resource not found, or not owned by this token's user | `{"detail":"Agent not found"}` |
| 405 | string | Method not allowed | `{"detail":"Method not allowed"}` |
| 409 | string | Conflict — see table below | `{"detail":"Version mismatch: expected 3, got 2"}` |
| 422 | **list** | Pydantic validation failure | `{"detail":[{"type":"missing",...}]}` |
| 502 | string | Sprites upstream error during session create | `{"detail":"Failed to create Sprite: connection refused"}` |

## 409 conflict cases

All of these return `409`:

| Action | Condition | `detail` |
|--------|-----------|---------|
| `POST /agents/{id}/archive` | Agent already archived | `"Agent is already archived"` |
| `PUT /agents/{id}` | Agent is archived | `"Cannot update an archived agent"` |
| `PUT /agents/{id}` | Version mismatch | `"Version mismatch: expected N, got M"` |
| `POST /environments/{id}/archive` | Environment already archived | `"Environment is already archived"` |
| `PUT /environments/{id}` | Environment is archived | `"Cannot update an archived environment"` |
| `PUT /environments/{id}` | Version mismatch | `"Version mismatch: expected N, got M"` |
| `DELETE /environments/{id}/delete` | Sessions reference this environment | `"Cannot delete environment with existing sessions"` |
| `POST /sessions` | Agent is archived | `"Cannot create session with archived agent"` |
| `POST /sessions` | Environment is archived | `"Cannot create session with archived environment"` |
| `POST /sessions/{id}/prompt` | Session is running | `"Session is already running"` |
| `POST /sessions/{id}/prompt` | Session has failed | `"Session has failed and cannot be resumed. Start a new session."` |
| `POST /sessions/{id}/prompt` | Session is terminated | `"Session has been terminated"` |
| `POST /sessions/{id}/terminate` | Session already terminated | `"Session is already terminated"` |
| `DELETE /sessions/{id}/delete` | Session is active (pending or running) | `"Cannot delete an active session"` |

## Notes

- **404 vs 401**: Agent on Demand never returns 403. Resources that exist but belong to a different user are returned as 404.
- **No 204**: deletes return `200` with a `{"detail":"..."}` body, not `204 No Content`.
- **Delete of missing resource**: returns `404`.
- **502**: only from session creation when the Sprites API is unreachable or returns an error.
