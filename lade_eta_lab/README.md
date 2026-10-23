# LaDe ETA Reliability Lab

This is the complete Case-A-Thon artifact for the prompt on unintentional data poisoning. It predicts delivery duration, isolates stale and low-quality data, compares a transparent countermeasure on the same future orders, and gives a dispatcher an evidence-based correction path. It runs locally with FastAPI and online at `antydata.com` through a Cloudflare Worker.

The website is intentionally written for a nontechnical audience. Technical details, machine-readable results, limitations, and governance records remain available in the Documentation section.

## What is included

| Area | What it provides |
|---|---|
| FastAPI backend | Live model inference, blind outcome reveal, results, timeline, case review, decision logging, documentation, and downloads |
| Experiment pipeline | Leakage-aware features, chronological splits, four degradation conditions, mitigation, repeated seeds, and saved model artifacts |
| Website | Plain-language conclusion, three-step live AI prediction, prepared-CSV batch upload, full experiment controls, mechanism trace, dispatcher walkthrough, interactive impact roadmap, scenario videos, and documentation tabs |
| Evidence package | Run-level and daily CSV files, data manifest, modification log, metric dictionary, governance plan, and AI-use disclosure |
| Walkthrough support | Data-generated roadmap videos, a dispatcher-facing report, and reproducible frame-exact MP4 rendering |
| Online deployment | Edge-hosted site and API, live exported-tree inference, and durable review decisions |

## Run online

The repository includes `wrangler.jsonc`, the Worker API, exported fitted trees,
and a static build script. Cloudflare Workers Builds should use `npm run build`
as the build command and `npx wrangler deploy` as the deploy command. See
`CLOUDFLARE_DEPLOYMENT.md` for the one-time account settings and verification
steps.

## Run locally

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
pytest
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`. Interactive API documentation is at `http://127.0.0.1:8000/docs`.

If you open the root `index.html` through VS Code Live Server, the complete CSS and JavaScript now load from relative paths. That view automatically connects to FastAPI at `http://127.0.0.1:8000`; if the backend is stopped, the polished design remains visible and a yellow banner shows the command needed to enable live data.

The demonstration is a live walkthrough of the working platform, not a slideshow. The Impact Roadmap section plays data-generated scenario briefings inside the site and links to the dispatcher-facing report. `DEMO_SCRIPT.md` provides the live route and technical fallback.

## Reproduce the measured experiment

The local working copy contains `data/raw/delivery_jl.csv`, an untracked LaDe city file. Raw third-party data remains excluded from Git.

```bash
source .venv/bin/activate
python run_experiments.py --data data/raw/delivery_jl.csv --max-rows 60000 --seeds 17 29 43
```

The run writes:

- `results/runs.csv`: one row per seed, condition, and mitigation state.
- `results/summary.csv`: mean and standard deviation across seeds.
- `results/timeline.csv`: daily error, age, source share, reliability, coverage, and counts.
- `results/predictions.csv`: fixed-test-set predictions used by the dispatcher demo.
- `artifacts/*.joblib`: the first-seed fitted models for each comparison.

## Safe synthetic preview

To check the pipeline without the source file:

```bash
python run_experiments.py --synthetic --max-rows 5000 --seeds 17
```

Synthetic output is labeled `SYNTHETIC_DEMO` and must not replace measured LaDe evidence in the live walkthrough. The website also exposes `POST /api/experiments/preview`; that endpoint runs in memory and does not overwrite the measured files.

## Experiment in plain language

The model estimates delivery minutes when a courier accepts an order. Data is split by whole days: the first 60 percent for training, the next 20 percent for validation, and the final 20 percent for the previously unseen evaluation period.

The fixed evaluation orders are tested under eight matched conditions:

1. Reference with the countermeasure off and on.
2. Stale area history with the countermeasure off and on.
3. A low-reliability high-volume feed with the countermeasure off and on.
4. Both defects together with the countermeasure off and on.

The main metric is mean absolute error in minutes. The artifact also reports large-error frequency, run-to-run variation, output coverage, and human review workload.

## What the live AI demo actually does

The website’s **Live AI prediction** section evaluates trees exported from the real fitted `HistGradientBoostingRegressor`. Online requests run in Cloudflare Workers; local model development can use FastAPI. The three buttons compare the same held-out delivery and fixed real outcome:

1. **Trusted pipeline:** the reference fitted model receives the untouched held-out inputs.
2. **Full degraded pipeline:** a model fitted on the controlled unreliable-feed training condition receives that delivery’s corresponding degraded inputs.
3. **Degraded pipeline + safeguard:** the reliability-aware fitted model applies weighted training, robust input backoff, and the human-review rule to the same degraded condition.

Training is performed ahead of the live walkthrough so the interaction is fast and repeatable. Every online button request freshly evaluates the exported fitted trees for the selected delivery. Safeguard preprocessing uses the same fixed evaluation-batch median registered by the Python experiment. The outcome is withheld from both the case list and prediction responses; the browser requests it from a separate retrospective reveal endpoint only after showing the trusted and degraded predictions.

The low-quality scenario is an **end-to-end scenario**, not an attribution test: it bundles simulated corruption of duplicated training rows with controlled changes to selected evaluation inputs. The four selectable cases are disclosed teaching examples, including a counterexample. They do not replace the aggregate conclusion from 3,407 held-out deliveries and 24 matched runs.

The upload panel accepts up to 50 prepared feature rows and runs every row through the same three fitted artifacts. A downloadable sample shows the accepted schema. For uploaded rows, the degraded comparison uses a disclosed deterministic version of the low-quality profile (`distance_km × 1.42`, `area_historical_eta + 10`, source reliability `0.25`, and unreliable-feed share `0.55`). Optional `duration_minutes` values are withheld from model inputs and used only to report retrospective error.

## Current measured conclusion

The low-quality feed raises mean ETA error by about 2.1 minutes versus the reference. The tested fix recovers about 0.5 minutes in that condition but does not restore reference performance. The stale-only condition does not worsen error, and the current review threshold defers every stale and combined case. These are material limits, so the recommendation is no deployment yet.

The bounded next step is to recalibrate deferral, test subgroup coverage, and then consider a four-week one-region shadow pilot with a named data steward, model owner, operations lead, and go/no-go checkpoint.

## Important boundaries

- The source is historical courier data, not autonomous-vehicle validation.
- Introduced defects are controlled experiments, not claims about Cainiao operations.
- The source license signals conflict; do not redistribute the raw CSV until the applicable terms are resolved.
- Prediction intervals are empirical validation-residual bands, not formal guarantees.
- A reviewer action in the website is a demonstration record, not a live operational decision.
- The live examples illustrate model behavior; only the complete matched experiment supports claims about average harm or mitigation.

## Project map

```text
app.py                     FastAPI application
static/                    Website styles and behavior
data.py                    Source loader and synthetic fallback
features.py                Acceptance-time features and chronological split
scenarios.py               Controlled degradation interventions
model.py                   ETA model, intervals, and metrics
mitigation.py              Weighting, safe backoff, and review policy
evaluate.py                Complete 4 × 2 runner
run_experiments.py         Command-line entry point
tests/                     Pipeline and API tests
results/                   Machine-readable evidence and local review log
DATA_MANIFEST.md           Provenance, filters, split, and rights notes
EXPERIMENT_CARD.md         Decision, hypotheses, methods, findings, limits
GOVERNANCE.md              Owners, stop conditions, and pilot checkpoint
AI_USE_DISCLOSURE.md       Evaluated AI and generative-AI disclosure
ARCHITECTURE.md            Data and service architecture
DEMO_SCRIPT.md             Live walkthrough and fallback recording plan
API.md                     Endpoint reference
../roadmap-section.html    Interactive impact-roadmap and scenario-video player
../roadmap-data.js         Roadmap assumptions, gates, and live-data aggregation
../user-report.html        Dispatcher-facing plain-language report
../render_videos.py        Frame-exact MP4 renderer for every roadmap program
```
