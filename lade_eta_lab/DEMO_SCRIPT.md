# Live demo and fallback recording script

## Ten-minute run of show

| Time | Screen | Presenter action and point |
|---|---|---|
| 0:00–0:35 | Presentation slide 1 | Ask when more information can make a decision worse |
| 0:35–1:15 | Overview | Define dispatcher, ETA, consequence of error, evaluation case, and MAE |
| 1:15–1:55 | Documentation data tab | Show source, date range, row count, filters, license conflict, and chronological split |
| 1:55–2:40 | Experiment lab reference | Establish the repeatable result and fixed cases |
| 2:40–3:35 | Low quality, fix off | Show the measured error increase as feed share grows |
| 3:35–4:35 | Live AI prediction | Run trusted, full degraded, and safeguarded inference on one held-out delivery; reveal the outcome only after the first comparison |
| 4:35–5:05 | Mechanism and safeguard | Clarify that the low-quality test bundles retraining plus degraded inputs; explain weighting, safe backoff, and deferral |
| 5:05–6:05 | Eight-way comparison | State the modest recovery and the stale/combined failures clearly |
| 6:05–8:10 | Human oversight | Inspect one flagged order and record a correction or escalation |
| 8:10–10:00 | Recommendation and governance | No deployment; recalibrate, subgroup test, then bounded shadow pilot |

## Live AI walkthrough

1. Keep **Delivery A · recommended walkthrough** selected.
2. Click **Run normal AI**. Say: “The model was trained beforehand, but this prediction is being recalculated now. The real outcome is still hidden.”
3. Click **Add unreliable data**. Say: “This is the registered full low-quality pipeline: controlled unreliable rows changed training, and the matching held-out inputs changed too. I am not claiming this isolates one mechanism.”
4. Read the plain-language result and reveal. Point out the prediction shift, actual duration, and change in absolute miss.
5. Click **Apply safeguard**. Explain the bundled policy and whether it recovered error and/or deferred to a person.
6. Switch to **Delivery D · counterexample** if time permits. State: “Not every delivery worsens. That is why the 3,407-case aggregate—not this curated example—supports the recommendation.”

The model artifacts are fitted in advance. Each click performs fresh FastAPI inference against the same fixed held-out batch and returns one package. The outcome endpoint is not called until after the trusted-versus-degraded comparison.

## Recorded fallback demo

The website includes an `Auto-play slides` button in the Required Files section and a 75-second captioned MP4 at `demo/fallback_demo.mp4`. The MP4 is silent and can be downloaded directly from the website. If spoken narration is desired, record the script below over the same visuals.

Suggested narration:

1. “We predict delivery minutes for a dispatcher using real, chronological LaDe records.”
2. “The controlled design keeps the future orders fixed and tests four data conditions with the fix off and on.”
3. “A high-volume unreliable feed raises ETA error, showing that more rows can make a decision worse.”
4. “Our reliability-aware fix helps slightly in that condition, but it fails to recover combined performance.”
5. “It also defers every stale case, so rejecting outputs cannot be called recovery.”
6. “The dispatcher sees the evidence and can accept, correct, or escalate; time and action are logged.”
7. “Our recommendation is no deployment yet: recalibrate, test subgroup coverage, and use a bounded shadow pilot.”

## Failure fallback

- If the API is running but a chart fails, use the eight-way results table in the Experiment card.
- If the local service fails, use the recorded auto-demo.
- If live inference is unavailable, use the recorded fallback, disclose that the models are pre-fitted, and show the saved machine-readable evidence plus the exact reproduction command.
- Never switch to synthetic output without displaying the synthetic label.
