# Online deployment at antydata.com

## Production design

`antydata.com` uses one Cloudflare Worker deployment:

- Workers Static Assets serves the website globally.
- Requests under `/api/*` wake and enter a Cloudflare Container.
- The container runs the existing FastAPI, pandas, and scikit-learn application.
- A Durable Object stores human-review decisions in persistent SQLite-backed storage.
- The container sleeps after 15 idle minutes to limit active compute usage.

The raw LaDe source file is not shipped. The image contains the three fitted
models, published result files, and a 3,407-row held-out inference bundle needed
by the live demonstration.

## One-time Cloudflare setup

1. Enable the Workers Paid plan because Containers require it.
2. In the existing Cloudflare Workers Builds connection, select this GitHub
   repository and the production branch `main`.
3. Keep the repository root as the build root.
4. Set the build command to `npm run build`.
5. Set the deploy command to `npx wrangler deploy`.
6. Confirm the dashboard Worker name is `antydata`. If an existing Worker uses a
   different name, change the `name` field in `wrangler.jsonc` to that exact name.
7. Confirm Containers are enabled for the account. The Wrangler configuration
   attaches `antydata.com` as the custom domain during deployment.

No application secrets are required for this demonstration. Each push to
`main` should build the static site, build the Linux container image, deploy the
Worker, and update the custom domain.

## Verification after deployment

Open these URLs in order:

1. `https://antydata.com/`
2. `https://antydata.com/api/health`
3. `https://antydata.com/api/live-demo/cases`

The first API request after an idle period can take a few seconds because it
starts the container. The site itself remains available from edge storage.

In the website, select a delivery and run the trusted, degraded, and safeguarded
predictions. Then save a human-review choice, reload the page, and confirm the
decision remains available. That final check verifies Durable Object storage,
not the container's temporary filesystem.

## Local Cloudflare-compatible check

Install a Docker-compatible engine and Node.js, then run:

```bash
npm install
npm run dev
```

Wrangler serves the site at the URL printed in the terminal. The original
FastAPI-only workflow remains available for model development inside
`lade_eta_lab/`.

## Rollback

Use the Cloudflare dashboard deployment history to roll back the Worker, or
revert the Git commit and push `main`. Do not switch production to a laptop or a
public tunnel as a fallback; those paths make availability depend on a personal
machine and do not provide the durable review log used here.
