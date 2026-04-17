# Pattern: Batch Automation

You need to run the same agent task against many inputs at once — processing a
list of files, generating content for a dataset, or applying automated fixes
across multiple repositories.

## Shape of the solution

Call `POST /sessions` for each item in your batch, run them concurrently with
a semaphore to stay within your Sprites quota and API rate limits, poll or
stream each session, then clean up when done.

Because each session runs in its own isolated Sprite, fairy handles parallelism
naturally — the main challenge is staying within the concurrency limits of
both Sprites and the underlying model runtime.

## Example

```python
import asyncio
import httpx

import os

FAIRY_URL = os.environ["FAIRY_URL"]
FAIRY_TOKEN = os.environ["FAIRY_TOKEN"]
AGENT_ID = os.environ["FAIRY_AGENT_ID"]
MAX_CONCURRENT = 5   # tune to your Sprites quota

async def run_session(client: httpx.AsyncClient, prompt: str) -> str:
    headers = {"Authorization": f"Bearer {FAIRY_TOKEN}"}

    # Create session
    r = await client.post(
        f"{FAIRY_URL}/sessions",
        json={"agent_id": AGENT_ID, "prompt": prompt, "timeout": 300},
        headers=headers,
    )
    r.raise_for_status()
    session_id = r.json()["id"]

    # Poll until terminal
    for _ in range(120):
        await asyncio.sleep(3)
        status_r = await client.get(
            f"{FAIRY_URL}/sessions/{session_id}", headers=headers
        )
        status_r.raise_for_status()
        status = status_r.json()["status"]
        if status in ("completed", "failed", "terminated"):
            break

    # Collect output via SSE stream
    output_lines = []
    async with client.stream(
        "GET",
        f"{FAIRY_URL}/sessions/{session_id}/stream",
        headers={**headers, "Accept": "text/event-stream"},
        timeout=None,
    ) as stream_r:
        async for line in stream_r.aiter_lines():
            if line.startswith("data: "):
                output_lines.append(line[6:])

    # Clean up session and Sprite
    await client.request(
        "DELETE",
        f"{FAIRY_URL}/sessions/{session_id}/delete",
        headers=headers,
    )

    return "\n".join(output_lines)

async def batch(prompts: list[str]) -> list[str]:
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient(timeout=30) as client:
        async def bounded(prompt):
            async with sem:
                return await run_session(client, prompt)
        return await asyncio.gather(*[bounded(p) for p in prompts])

if __name__ == "__main__":
    items = ["Summarise file A", "Summarise file B", "Summarise file C"]
    results = asyncio.run(batch(items))
    for prompt, result in zip(items, results):
        print(f"--- {prompt} ---\n{result}\n")
```

## Handling stuck sessions

Sessions can get stuck in `running` or `pending` state if a Sprite crashes or
the underlying model times out. Add a hard deadline in your polling loop and
terminate sessions that exceed it:

```python
# Terminate a stuck session
await client.post(
    f"{FAIRY_URL}/sessions/{session_id}/terminate",
    headers=headers,
)
```

`POST /sessions/{id}/terminate` returns `409` if the session is already
terminated — treat that as a safe no-op.

## Trade-offs

| | |
|---|---|
| **Semaphore** | Controls in-flight sessions; set `MAX_CONCURRENT` based on your Sprites plan and model rate limits. |
| **Polling vs streaming** | Polling (`GET /sessions/{id}`) is simpler for batch; streaming is better when you want incremental output. |
| **Cleanup** | Always call `DELETE /sessions/{id}/delete` after processing — terminated Sprites still count against your quota until deleted. |
| **Error handling** | `asyncio.gather` with `return_exceptions=True` prevents one failure from cancelling the whole batch. |
| **Cost** | Each session creates and runs a Sprite — budget per-item before launching large batches. |
