# API reference

The local API is available at `http://127.0.0.1:8000`. FastAPI’s interactive OpenAPI page is at `/docs`.

## Evidence endpoints

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Service and artifact readiness |
| `GET /api/overview` | Dataset profile, main finding, experiment metadata, and requirement coverage |
| `GET /api/comparison` | Eight-condition summary with run variation |
| `GET /api/timeline` | Daily metrics for one condition and mitigation state |
| `GET /api/cases` | Fixed evaluation cases for the dispatcher walkthrough |

Timeline parameters are `condition` and `mitigated`. Case parameters also include `flagged_only` and `limit`.

## Live AI inference endpoints

| Method and path | Purpose |
|---|---|
| `GET /api/live-demo/cases` | List four disclosed, real held-out teaching cases without revealing their outcomes |
| `POST /api/live-demo/predict` | Recalculate one selected scenario and return its prediction, interval, review state, changed signals, model metadata, and caveats |
| `GET /api/live-demo/outcome/{package_id}` | Reveal the held-out outcome and scenario errors after the prediction comparison |

The prediction request body is:

```json
{
  "package_id": "482116",
  "scenario": "clean"
}
```

`scenario` must be `clean`, `poisoned`, or `safeguarded`. The endpoint loads the corresponding pre-fitted artifact and makes fresh `.predict()` calls over the fixed evaluation batch. It returns only the selected delivery’s output. This preserves the same batch context used by safeguard preprocessing in the measured experiment.

The `poisoned` scenario is the full low-quality condition: the model was fitted on controlled unreliable training rows, and the held-out delivery receives the corresponding controlled degraded inputs. The `safeguarded` scenario adds the bundled reliability-aware training, input backoff, and review policy. Neither `/cases` nor `/predict` returns the real duration. `/outcome/{package_id}` is a separate retrospective reveal, and the target is never a model feature.

## Human oversight endpoints

| Method and path | Purpose |
|---|---|
| `POST /api/decisions` | Validate and append an accept, override, or escalation action |
| `GET /api/decisions` | Read recent local demonstration actions |

An override requires a corrected ETA from 1 to 360 minutes. Accept and escalate actions must not include a corrected ETA. Each record stores the original model ETA, reviewer label, note, and human review seconds.

## Preview endpoint

| Method and path | Purpose |
|---|---|
| `POST /api/experiments/preview` | Run a labeled synthetic sensitivity preview in memory |

The preview endpoint does not overwrite measured results.

## Documentation and downloads

| Method and path | Purpose |
|---|---|
| `GET /api/docs` | List documentation tabs |
| `GET /api/docs/{document_id}` | Read a Markdown document |
| `GET /api/downloads/{filename}` | Download an allow-listed result or manifest file |

Only allow-listed filenames can be downloaded. Arbitrary file paths are rejected.
