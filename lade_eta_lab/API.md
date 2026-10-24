# API reference

The production API is available at `https://antydata.com/api/*`. The local API is available at `http://127.0.0.1:8000`. FastAPI’s interactive OpenAPI page is available at `/docs` when running FastAPI directly.

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
| `POST /api/live-demo/upload-predict` | Score 1–1,000 prepared CSV rows through the trusted, unreliable-feed, and safeguarded pipelines in one request |
| `GET /api/data-reviews` | Read recent signed, item-level human data-review records |
| `POST /api/data-reviews` | Validate every changed signal against server-derived evidence and persist the signed decision |
| `GET /api/live-demo/outcome/{package_id}` | Reveal the held-out outcome and scenario errors after the prediction comparison |

The prediction request body is:

```json
{
  "package_id": "482116",
  "scenario": "clean"
}
```

`scenario` must be `clean`, `poisoned`, or `safeguarded`. In production, the endpoint evaluates the corresponding exported fitted trees for the selected held-out delivery. The safeguarded vector retains the same fixed-batch preprocessing context used by the measured Python experiment. Local FastAPI development loads the original joblib artifact and calls `.predict()`.

The `poisoned` scenario is the full low-quality condition: the model was fitted on controlled unreliable training rows, and the held-out delivery receives the corresponding controlled degraded inputs. The `safeguarded` scenario adds the bundled reliability-aware training, input backoff, and review policy. Neither `/cases` nor `/predict` returns the real duration. `/outcome/{package_id}` is a separate retrospective reveal, and the target is never a model feature.

The upload endpoint accepts JSON produced by the website's CSV parser. Each row requires `region_code`, `aoi_type_code`, `accept_hour`, `day_of_week`, `distance_km`, `courier_recent_accepts`, `area_historical_eta`, and `courier_historical_eta`. `package_id`, `city`, `region_id`, and `duration_minutes` are optional. When duration is present, it is used only after prediction to calculate error. The uploaded baseline is scored as trusted input, then a disclosed deterministic low-quality profile is applied for the degraded and safeguarded comparison because new rows were not members of the original seeded evaluation batch.

## Human oversight endpoints

| Method and path | Purpose |
|---|---|
| `POST /api/decisions` | Validate and append an accept, override, or escalation action |
| `GET /api/decisions` | Read recent demonstration actions |

An override requires a corrected ETA from 1 to 360 minutes. Accept and escalate actions must not include a corrected ETA. Each record stores the original model ETA, reviewer label, note, and human review seconds.

In production, the Cloudflare Worker stores these records in Durable Object storage and generates the `overrides.csv` download from the same store. In local development, FastAPI stores them in `results/overrides.csv`.

## Preview endpoint

| Method and path | Purpose |
|---|---|
| `POST /api/experiments/preview` | Run a labeled synthetic sensitivity preview in local FastAPI development |

The preview endpoint does not overwrite measured results. It returns `501` in the public Worker because on-demand model retraining is intentionally kept in the local Python research environment.

## Documentation and downloads

| Method and path | Purpose |
|---|---|
| `GET /api/docs` | List documentation tabs |
| `GET /api/docs/{document_id}` | Read a Markdown document |
| `GET /api/downloads/{filename}` | Download an allow-listed result or manifest file |

Only allow-listed filenames can be downloaded. Arbitrary file paths are rejected.
