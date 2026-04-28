# Pattern: CI Bot

You want CI to automatically review a pull request, suggest fixes, or run an
analysis task — without a human in the loop.

## Shape of the solution

In your CI job, collect the context you care about (PR diff, test output, lint
errors) and post it as a prompt to `POST /sessions`. Poll `GET /sessions/{id}`
until the status is terminal, then read the output from
`GET /sessions/{id}/stream` and post it as a PR comment.

CI runs are short-lived and stateless, so prefer single-shot sessions over
multi-turn. You don't need to call `POST /sessions/{id}/prompt` or manage
session continuations.

Teams that already use Python in CI can run the same flow through
[`aod-sdk`](../sdks/python.md) — see the [CLI Wrapper](cli-wrapper.md) pattern
for an SDK-based equivalent.

## Example (GitHub Actions)

```yaml
name: AI Code Review

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  agent-on-demand-review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run Agent on Demand review
        env:
          AOD_TOKEN: ${{ secrets.AOD_TOKEN }}
          AOD_URL: ${{ secrets.AOD_URL }}
          AGENT_ID: ${{ secrets.AOD_AGENT_ID }}
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          DIFF=$(git diff origin/${{ github.base_ref }}...HEAD -- '*.py' | head -c 8000)
          PROMPT="Review the following Python diff for bugs, style issues, and security problems:\n\n${DIFF}"

          # Create session
          SESSION=$(curl -sS -X POST "$AOD_URL/sessions" \
            -H "Authorization: Bearer $AOD_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"agent_id\":\"$AGENT_ID\",\"prompt\":\"$PROMPT\",\"timeout\":300}")
          SESSION_ID=$(echo $SESSION | jq -r .id)

          # Poll until done
          for i in $(seq 1 60); do
            STATUS=$(curl -sS "$AOD_URL/sessions/$SESSION_ID" \
              -H "Authorization: Bearer $AOD_TOKEN" | jq -r .status)
            [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] && break
            sleep 5
          done

          # Collect output and post as PR comment
          # SSE events: keep only "output" type, extract the .data field
          OUTPUT=$(curl -sS "$AOD_URL/sessions/$SESSION_ID/stream" \
            -H "Authorization: Bearer $AOD_TOKEN" \
            -H "Accept: text/event-stream" \
            | grep '^data: ' | sed 's/^data: //' \
            | jq -r 'select(.type == "output") | .data' | tr -d '\0')

          gh pr comment ${{ github.event.pull_request.number }} --body "$OUTPUT"
```

## Trade-offs

| | |
|---|---|
| **Single-shot** | CI sessions don't need continuations — create once, read once, discard. |
| **Cost** | Each CI run spins up a Sprite. Budget accordingly; skip on draft PRs or for trivial changes. |
| **Timeout** | Set `timeout` to a value well under your CI job timeout so the session fails cleanly. |
| **Diff size** | Truncate large diffs before sending — very long prompts slow the agent and increase cost. |
| **Cleanup** | Sessions persist in Agent on Demand after CI ends. Add a nightly job calling `DELETE /sessions/{id}/delete` to prune old ones. |
| **Security** | Store `AOD_TOKEN` as a GitHub Actions secret, never in the workflow file. |
