# Pattern: Batch Automation

You need to run the same agent task against many inputs at once — processing a
list of files, generating content for a dataset, or applying automated fixes
across multiple repositories.

Python examples use the official [`aod-sdk`](../sdks/python.md) package
(`pip install aod-sdk`).

## Shape of the solution

Call `client.sessions.create(...)` for each item in your batch, run them
concurrently with a semaphore to stay within your Sprites quota and API rate
limits, poll or stream each session, then clean up when done.

Because each session runs in its own isolated Sprite, Agent on Demand handles parallelism
naturally — the main challenge is staying within the concurrency limits of
both Sprites and the underlying model runtime. `AsyncClient` is the natural fit
here: one client, many concurrent tasks.

## Example

```python
import asyncio
import os

from aod import AodError, AsyncClient, ConflictError

AGENT_ID = os.environ["AOD_AGENT_ID"]
MAX_CONCURRENT = 5   # tune to your Sprites quota

async def run_session(client: AsyncClient, prompt: str) -> str:
    ack = await client.sessions.create(
        agent_id=AGENT_ID, prompt=prompt, timeout=300
    )
    session_id = str(ack.id)

    # Poll until terminal
    for _ in range(120):
        await asyncio.sleep(3)
        session = await client.sessions.get(session_id)
        if session.status in ("completed", "failed", "terminated"):
            break

    # Collect output via SSE stream
    output_parts: list[str] = []
    async with client.sessions.stream(session_id) as events:
        async for event in events:
            if event.type == "output" and event.extra.get("stream") == "stdout":
                output_parts.append(event.extra.get("data", ""))
            elif event.type in ("exit", "error", "terminated", "stale"):
                break

    # Clean up session and Sprite
    try:
        await client.sessions.delete(session_id)
    except ConflictError:
        pass  # already running/deleted — safe no-op

    return "".join(output_parts)

async def batch(prompts: list[str]) -> list[str | BaseException]:
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with AsyncClient() as client:  # reads AOD_API_URL + AOD_API_TOKEN
        async def bounded(prompt: str) -> str:
            async with sem:
                return await run_session(client, prompt)

        return await asyncio.gather(
            *[bounded(p) for p in prompts],
            return_exceptions=True,   # one failure shouldn't cancel the batch
        )

if __name__ == "__main__":
    items = ["Summarise file A", "Summarise file B", "Summarise file C"]
    results = asyncio.run(batch(items))
    for prompt, result in zip(items, results):
        if isinstance(result, BaseException):
            print(f"--- {prompt} --- FAILED: {result!r}\n")
        else:
            print(f"--- {prompt} ---\n{result}\n")
```

## Handling stuck sessions

Sessions can get stuck in `running` or `pending` state if a Sprite crashes or
the underlying model times out. Add a hard deadline in your polling loop and
terminate sessions that exceed it:

```python
from aod import ConflictError

try:
    await client.sessions.terminate(session_id)
except ConflictError:
    pass  # already terminated — safe no-op
```

## Trade-offs

| | |
|---|---|
| **Semaphore** | Controls in-flight sessions; set `MAX_CONCURRENT` based on your Sprites plan and model rate limits. The server also enforces a per-user cap — overshoot raises `RateLimitError` (`.limit`, `.active`). |
| **Polling vs streaming** | `client.sessions.get(id).status` is simpler for batch; streaming is better when you want incremental output. |
| **Cleanup** | Always call `client.sessions.delete(id)` after processing — terminated Sprites still count against your quota until the session row is gone. |
| **Error handling** | `asyncio.gather(..., return_exceptions=True)` prevents one failure from cancelling the whole batch. Errors come back as `AodError` subclasses. |
| **Cost** | Each session creates and runs a Sprite — budget per-item before launching large batches. |
