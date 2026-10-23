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

## Backend boundary

FastAPI reads saved experiment artifacts and exposes narrow JSON endpoints. It does not send source data to external services. A dispatcher action is appended to `results/overrides.csv` under a process lock.

For the live AI comparison, FastAPI recreates the seed-17 chronological held-out frames, loads the corresponding fitted `FittedETA` artifact, and recalculates predictions over the full fixed evaluation batch. Only the selected package is returned. Full-batch scoring preserves the stable median context currently used by safeguard preprocessing; scoring a single row would not reproduce the registered experiment. The models are fitted beforehand, but prediction is computed on every request.

The `clean` route uses the reference artifact and untouched evaluation frame. The `poisoned` route mirrors the complete low-quality condition: controlled unreliable training rows plus matching degraded evaluation inputs. The `safeguarded` route uses that degraded frame with the bundled weighted-training, robust-backoff, and review policy. This design supports end-to-end scenario comparison, not causal attribution between training and input corruption.

Case discovery and prediction responses omit the outcome. A separate reveal endpoint returns it only for retrospective evaluation after the interface has shown the comparison. `duration_minutes` is not in `MODEL_FEATURES` and never enters the scikit-learn prediction matrix.

## Data boundary

The raw LaDe CSV and fitted model files are local and ignored by Git. Machine-readable evidence contains aggregate results plus evaluation-case predictions required for the local walkthrough. Public deployment would require a separate decision about dataset rights, authentication, durable storage, audit retention, and access control.

## Reproducibility controls

- Fixed seeds 17, 29, and 43.
- Whole-day chronological splits.
- Same evaluation order identifiers and outcomes in every comparison.
- Introduced changes separated from source observations.
- Experiment configuration stored as JSON.
- Raw source SHA-256 recorded in the data manifest.
- Tests for feature plausibility, split isolation, fixed cases, model fitting, APIs, and decision validation.
- Live-inference tests that match the registered saved predictions, verify real `.predict()` calls, hide the target before reveal, and reject unpublished cases.

## Local service

The service runs with `uvicorn app:app --reload` and serves both the API and the static interface. OpenAPI documentation is generated automatically at `/docs`.

This local architecture is appropriate for the competition artifact. Moving online would require authenticated users, a database-backed decision log, encrypted storage, rate limits, monitoring, and an explicit data-license review.
