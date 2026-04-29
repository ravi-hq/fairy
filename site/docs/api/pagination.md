# Pagination

## List envelope

All list endpoints return a JSON object with a `data` array:

```json
{"data": [...]}
```

Single-resource endpoints (`GET /agents/{id}`, `GET /sessions/{id}`, etc.) return the object directly, with no envelope.

## No pagination

Agent on Demand's list endpoints return all results in a single response — there is no cursor, no page token, and no `limit`/`offset` parameter.

| Endpoint | Returns |
|----------|---------|
| `GET /agents` | All non-archived agents owned by this token's user, ordered by `-created_at` |
| `GET /environments` | All non-archived environments owned by this token's user, ordered by `-created_at` |
| `GET /sessions` | All sessions owned by this token's user (including `completed`, `failed`, `terminated`), ordered by `-created_at` |
| `GET /agents/{id}/versions` | All version snapshots for the agent, newest first |
| `GET /environments/{id}/versions` | All version snapshots for the environment, newest first |

## Archived resources

- Archived agents and environments are **excluded** from list endpoints. They are still accessible via `GET /agents/{id}` or `GET /environments/{id}`.
- Sessions have no archive concept. All sessions (including terminal ones) appear in `GET /sessions`. Use `DELETE /sessions/{id}/delete` to remove sessions you no longer need.

## Example

```bash
# List all agents
curl -H "Authorization: Bearer $TOKEN" "$BASE/agents"
```

```json
{
  "data": [
    {
      "id": "3f2a1b4c-...",
      "type": "agent",
      "name": "code-reviewer",
      "model": "anthropic/claude-sonnet-4-6",
      "runtime": "claude",
      "version": 2,
      "created_at": "2026-04-17T14:00:00.000000+00:00",
      "updated_at": "2026-04-17T15:00:00.000000+00:00",
      "archived_at": null
    }
  ]
}
```
