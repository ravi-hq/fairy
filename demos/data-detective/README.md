# Data Detective — Demo POC

An interactive web app where you select a sample dataset and an AI agent narrates the story in the data in real time, then you can ask follow-up questions in a multi-turn conversation.

## What it demonstrates

- **Streaming analysis**: the agent narrates findings sentence-by-sentence as it "discovers" them, simulating an AoD (Agent on Demand) runtime startup + output stream
- **Hidden anomalies**: each dataset has a baked-in story the agent surfaces (a revenue spike, a funnel drop-off cliff, API tail latency)
- **Multi-turn conversation**: follow-up answers reference specific numbers from the initial analysis, showing conversational memory

## Datasets

| Dataset | Rows | Hidden story |
|---|---|---|
| E-commerce Sales | 30 | Tuesday 2024-03-12 revenue spike 8x normal; Electronics declining; West region underperforming |
| User Signup Funnel | 7 | 62% drop-off at "Add Payment Method" — 3-4x worse than every other step |
| API Response Times | 20 | `/api/search` p99 of 8,400ms; `/api/export` 4.2% error rate; two dead-code endpoints |

## Running locally

```bash
cd demos/data-detective
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open http://localhost:8000.

## API routes

| Method | Route | Description |
|---|---|---|
| GET | `/analysis/datasets` | List all datasets with metadata and 8-row preview |
| GET | `/analysis/stream/{dataset_id}` | SSE: stream initial analysis |
| POST | `/analysis/followup/{dataset_id}` | SSE: stream follow-up answer (`{"question": "..."}`) |

## Architecture

```
app.py          FastAPI app — routes, SSE stream wrappers
datasets.py     Dataset rows, column schemas, narrative analysis text,
                pre-scripted follow-up Q&A with keyword matching
static/
  index.html    Split-panel UI — dataset cards + preview table (left),
                streaming narrative + conversation history (right)
```

The mock AoD client (`mock_analysis_stream`, `mock_followup_stream`) simulates the three-stage startup sequence (`create_sprite → provision_setup → runtime_start`) then streams sentences with realistic delays. Follow-up responses are matched by keyword to pre-scripted answers that reference numbers from the initial analysis.
