# Architecture

## System flow

```text
Untouched LaDe city CSV
        ↓
Schema and outcome checks
        ↓
Leakage-aware acceptance-time features
        ↓
Whole-day chronological train / validation / evaluation split
        ↓
Reference · stale · low-quality · combined scenarios
        ↓
Gradient-boosted ETA model, fix off / on, three seeds
        ↓
Run CSVs · daily timeline · fixed-case predictions · fitted model artifacts
        ↓
FastAPI model lab · exported fitted trees · blind outcome reveal · evidence
        ↓
Cloudflare Worker API · Durable Object review log · static website
        ↓
Live AI comparison · dispatcher review · docs · presentation
```

## Online service boundary

Cloudflare Workers Static Assets serves the browser interface at `antydata.com`. The same Worker answers same-origin requests under `/api/*`; no laptop, tunnel, Python process, or Cloudflare Container is needed in production.

In production, the Worker validates a dispatcher action and stores it in Durable Object storage. Reads and the `overrides.csv` download come from that durable store. In local FastAPI development, the equivalent validated action is appended to `results/overrides.csv` under a process lock.

For the live AI comparison, a checked-in export step converts each fitted seed-17 scikit-learn `HistGradientBoostingRegressor` into compact split nodes, thresholds, imputation values, and leaf values. The Worker traverses those fitted trees for the selected held-out feature vector on every request. The safeguarded vector uses the stable evaluation-batch median calculated during the controlled Python experiment, so its preprocessing context remains identical to the registered result.

The `clean` route uses the reference artifact and untouched evaluation frame. The `poisoned` route mirrors the complete low-quality condition: controlled unreliable training rows plus matching degraded evaluation inputs. The `safeguarded` route uses that degraded frame with the bundled weighted-training, robust-backoff, and review policy. This design supports end-to-end scenario comparison, not causal attribution between training and input corruption.

Case discovery and prediction responses omit the outcome. A separate reveal endpoint returns it only for retrospective evaluation after the interface has shown the comparison. `duration_minutes` is not in `MODEL_FEATURES` and never enters the scikit-learn prediction matrix.

## Data boundary

The raw LaDe CSV remains local and ignored by Git because the applicable redistribution terms must be resolved. Production contains team-created exported tree parameters, published machine-readable evidence, and four disclosed held-out feature examples. The runtime bundle is enough to calculate the live demo but not to reproduce model training from the third-party source.

## Reproducibility controls

- Fixed seeds 17, 29, and 43.
- Whole-day chronological splits.
- Same evaluation order identifiers and outcomes in every comparison.
- Introduced changes separated from source observations.
- Experiment configuration stored as JSON.
- Raw source SHA-256 recorded in the data manifest.
- Tests for feature plausibility, split isolation, fixed cases, model fitting, APIs, and decision validation.
- Live-inference tests that match the registered saved predictions, verify real `.predict()` calls, hide the target before reveal, and reject unpublished cases.

## Service modes

For local model work, FastAPI runs with `uvicorn app:app --reload` and serves both the API and interface. OpenAPI documentation is generated automatically at `/docs`.

For production, `wrangler deploy` builds the static asset bundle and edge API. It runs on the Workers Free plan and remains available independently of a personal computer. Cloudflare observability is enabled, and the human-review log uses persistent Durable Object storage. Authentication, retention policy, and an explicit data-license review are still required before using this beyond a public competition demonstration.
