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
FastAPI live inference · blind outcome reveal · evidence · decision endpoints
        ↓
Live AI comparison · experiment lab · dispatcher review · docs · presentation
```

## Online service boundary

Cloudflare Workers Static Assets serves the browser interface at `antydata.com`. Same-origin requests under `/api/*` go through a Worker to a single Cloudflare Container running FastAPI. The container has no outbound internet access and reads only the bundled fitted models, held-out inference features, and published experiment results.

In production, the Worker validates a dispatcher action through FastAPI and then stores the returned record in Durable Object storage. Reads and the `overrides.csv` download come from that durable store. In local FastAPI development, the same validated action is appended to `results/overrides.csv` under a process lock.

For the live AI comparison, FastAPI recreates the seed-17 chronological held-out frames, loads the corresponding fitted `FittedETA` artifact, and recalculates predictions over the full fixed evaluation batch. Only the selected package is returned. Full-batch scoring preserves the stable median context currently used by safeguard preprocessing; scoring a single row would not reproduce the registered experiment. The models are fitted beforehand, but prediction is computed on every request.

The `clean` route uses the reference artifact and untouched evaluation frame. The `poisoned` route mirrors the complete low-quality condition: controlled unreliable training rows plus matching degraded evaluation inputs. The `safeguarded` route uses that degraded frame with the bundled weighted-training, robust-backoff, and review policy. This design supports end-to-end scenario comparison, not causal attribution between training and input corruption.

Case discovery and prediction responses omit the outcome. A separate reveal endpoint returns it only for retrospective evaluation after the interface has shown the comparison. `duration_minutes` is not in `MODEL_FEATURES` and never enters the scikit-learn prediction matrix.

## Data boundary

The raw LaDe CSV remains local and ignored by Git because the applicable redistribution terms must be resolved. Production contains three team-created fitted model files, the published machine-readable evidence, and compressed feature-only copies of the fixed 3,407-row evaluation inputs. The runtime bundle is enough to recalculate the live demo but not to reproduce model training from the third-party source.

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

For production, `wrangler deploy` builds the static asset bundle and the Linux FastAPI container. The container sleeps after 15 idle minutes; the edge-hosted interface remains available during a cold start. Cloudflare observability is enabled, and the human-review log uses persistent Durable Object storage. Authentication, retention policy, and an explicit data-license review are still required before using this beyond a public competition demonstration.
