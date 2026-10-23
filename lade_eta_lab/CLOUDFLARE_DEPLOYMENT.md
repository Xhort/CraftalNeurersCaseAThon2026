# Online deployment at antydata.com

## Production design

`antydata.com` uses one Cloudflare Worker deployment that works on the Workers
Free plan:

- Workers Static Assets serves the website globally.
- The Worker answers every `/api/*` request at the edge.
- The three fitted scikit-learn gradient-boosted models are exported as compact
  decision trees. The Worker evaluates those trees for each live prediction.
- Published experiment evidence and documentation are bundled as immutable
  competition artifacts.
- A Durable Object stores human-review decisions in persistent SQLite-backed
  storage.

The raw LaDe source file is never shipped. Model training and experiment
reproduction remain in Python; public prediction inference runs in the Worker.
No laptop, tunnel, container, or paid Workers plan is required.

## Cloudflare build settings

1. Connect this GitHub repository and choose the production branch `main`.
2. Keep the repository root as the build root.
3. Set the build command to `npm run build`.
4. Set the deploy command to `npx wrangler deploy`.
5. Confirm the dashboard Worker name is `craftalneurerscaseathon2026`, matching
   `wrangler.jsonc`.

No application secrets are required. Each push to `main` rebuilds the site,
deploys the edge API, and updates `antydata.com`.

## Verification after deployment

Open these URLs in order:

1. `https://antydata.com/`
2. `https://antydata.com/api/health`
3. `https://antydata.com/api/live-demo/cases`

Then run the trusted, degraded, and safeguarded predictions on the website.
The health response should report `runtime: cloudflare-workers` and
`model_inference_ready: true`.

## Local Cloudflare-compatible check

With Node.js installed:

```bash
npm install
npm run dev
```

The original FastAPI workflow remains available in `lade_eta_lab/` for model
development, experiment regeneration, and OpenAPI inspection.

## Updating the exported models

After retraining the Python artifacts, regenerate the Worker bundle before
committing:

```bash
cd lade_eta_lab
source .venv/bin/activate
python scripts/export_worker_bundle.py
```

## Rollback

Use the Cloudflare deployment history to roll back the Worker, or revert the
Git commit and push `main`. Do not use a public tunnel as production hosting;
that would make availability depend on a personal computer.
