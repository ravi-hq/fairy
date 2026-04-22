# batch-automation

Run one agent prompt per line against Agent on Demand, concurrently. Reads
prompts from a file or stdin, creates a session per prompt (capped by a
semaphore), streams each one's output, and prints results to stdout as
`--- <prompt> ---\n<output>` blocks.

Built on [`aod-sdk`](../../clients/python/)'s async client. See the
[Batch Automation pattern](../../site/docs/patterns/batch-automation.md) for the
full write-up.

## Install

```bash
pip install aod-sdk
```

Python 3.11+ required.

## Configure

| Variable | Required | Default | What it does |
|---|---|---|---|
| `AOD_API_URL` | yes | — | Deployment URL, e.g. `https://aod.example` |
| `AOD_API_TOKEN` | yes | — | Bearer token (`aod_...`) |
| `AOD_AGENT_ID` | yes | — | Agent to run each prompt against |
| `AOD_MAX_CONCURRENT` | no | `5` | Cap on in-flight sessions |
| `AOD_TIMEOUT` | no | `300` | Per-session timeout (seconds) |
| `AOD_POLL_INTERVAL` | no | `3` | Seconds between status polls |
| `AOD_POLL_ATTEMPTS` | no | `120` | Max polls before giving up |

## Run

```bash
# From a file
./batch.py prompts.sample.txt

# From stdin
./batch.py < prompts.sample.txt
printf 'task a\ntask b\n' | ./batch.py
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | All prompts succeeded |
| `1` | No prompts to run (empty input) |
| `2` | At least one prompt failed — failed blocks are labeled `FAILED: <repr>` |

## What to look at

- `batch.py:run_one` — per-session flow: create → poll → stream stdout → delete.
- `batch.py:batch` — concurrency loop with `asyncio.Semaphore` and
  `asyncio.gather(return_exceptions=True)` so one failure doesn't cancel the batch.
- `batch.py:_drain_stdout` — the SDK's `StreamEvent` iterator makes SSE
  reading a four-line loop.

## Production notes

This is an example — simple on purpose. For real pipelines:

- Replace stdin with a queue (SQS, Redis Streams, etc.) so work survives
  restarts.
- Persist results instead of printing them — one row per (prompt, output).
- Wrap the inner retry loop around `RateLimitError` (HTTP 429); the server
  returns `.limit` and `.active` so you can back off precisely.
- Tune `AOD_MAX_CONCURRENT` to your Sprites quota and the underlying model's
  rate limit — whichever is tighter.
