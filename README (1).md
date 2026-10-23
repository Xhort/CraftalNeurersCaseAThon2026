# Impact Roadmap + Scenario Videos — drop-in for antydata.com

The section shows **one overview video plus one video per experiment scenario**
(trusted / old data / noisy feed / both problems — whatever conditions appear in
your data). Every number is generated at page load from the site's own
`/api/downloads/timeline.csv`, so when you re-run the experiment, the videos and
the user report update with no code changes.

| File | What it is |
|---|---|
| `roadmap-section.html` | The section: video picker, player, scene chips, narration, record buttons, roadmap cards. |
| `roadmap-data.js` | Loads + aggregates `timeline.csv`; holds wording, gates and planning assumptions. |
| `user-report.html` | Plain-language report for dispatchers, incl. an "every scenario at a glance" table from the live data. |
| `render_videos.py` | Renders every video to MP4 from the live page (frame-exact, ~3 min for all five). |

## Install (plain HTML site)
1. Put `roadmap-data.js` and `user-report.html` next to `index.html`.
2. Replace the "10-MINUTE PRESENTATION" block with the contents of `roadmap-section.html`.
3. Make sure `/api/downloads/timeline.csv` is served from the same domain (it already is on antydata.com).

React/Vite/Next: put the files in `public/`, wrap `roadmap-section.html` in
`<!doctype html><html><body style="margin:0">…</body></html>` as `public/roadmap-embed.html`,
and embed it with an `<iframe>`.

## Making the MP4s
- **In the browser:** pick a video → "Record this video", or "Record all videos" (Chrome/Edge; real time, keep the tab visible).
- **Script (recommended):**
  ```
  pip install playwright imageio-ffmpeg && python -m playwright install chromium
  python render_videos.py --url https://antydata.com/ --out videos
  ```
  Copy `videos/*.mp4` next to `fallback_demo.mp4` so they're served from `/api/downloads/`.

## How each scenario video decides its verdict (no hand-written conclusions)
- Safeguard sends ≥ 90% of deliveries to dispatchers → "handed to dispatchers" (not a fix)
- Safeguard makes average miss worse by > 0.05 min → "makes this scenario worse"
- Otherwise: % of the loss vs trusted data recovered, checked against the gates in `roadmap-data.js`
- Trusted-data scenario: checks the safeguard stays neutral (±0.5 min) and compares with the simple no-AI baseline

If `timeline.csv` can't be fetched, the overview still plays from `roadmap-data.js`
and a "Load timeline.csv" button lets you load the file by hand.
