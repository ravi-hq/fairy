# Authentication

## Bearer tokens

Every request to Agent on Demand — except `GET /health` — must include an `Authorization` header:

```
Authorization: Bearer aod_<token>
```

Token strings always begin with `aod_`. They are created server-side via `APIKey.create_key(user, name)`. The raw token string is shown only once at creation time; there is no way to retrieve it afterward.

## 401 responses

Any authentication failure returns **401** with a JSON body containing a `detail` string. There are four possible messages:

| Condition | `detail` |
|-----------|---------|
| Header missing or not in `Bearer <token>` format | `"Missing or invalid Authorization header"` |
| Token not found in the database | `"Invalid API key"` |
| Token exists but has been deactivated | `"API key is inactive"` |
| Token exists but its expiry date has passed | `"API key has expired"` |

Example response body:

```json
{"detail": "Invalid API key"}
```

## No 403

Agent on Demand does not return 403. Resources that exist but don't belong to the authenticated user are returned as 404 (same as not found), preventing enumeration.

## Example

=== "curl"

    ```bash
    # Missing header → 401
    curl http://localhost:8777/agents
    # {"detail":"Missing or invalid Authorization header"}

    # Valid token
    curl http://localhost:8777/agents \
      -H "Authorization: Bearer aod_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    # {"data":[...]}
    ```

=== "Python"

    With [`aod-sdk`](../sdks/python.md), the token is set at `Client` construction (or via `AOD_API_TOKEN`); 401 responses surface as `AuthError`:

    ```python
    from aod import AuthError, Client

    client = Client(
        base_url="http://localhost:8777",
        token="aod_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    )
    try:
        agents = client.agents.list()
    except AuthError as e:
        print(e.status_code, e.detail)
        # 401 'Invalid API key'
    ```

    `AuthError` is a subclass of `AodHTTPError` — `.status_code`, `.detail`, `.method`, `.url` are all available.
