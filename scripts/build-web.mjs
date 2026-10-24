import { cp, mkdir, rm } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const dist = resolve(root, "dist");

await rm(dist, { recursive: true, force: true });
await mkdir(resolve(dist, "lade_eta_lab"), { recursive: true });
await mkdir(resolve(dist, "download-assets"), { recursive: true });
await mkdir(resolve(dist, "support"), { recursive: true });
await cp(resolve(root, "index.html"), resolve(dist, "index.html"));
for (const filename of ["roadmap-section.html", "roadmap-data.js", "user-report.html"]) {
  await cp(resolve(root, filename), resolve(dist, filename));
}
await cp(
  resolve(root, "lade_eta_lab/static"),
  resolve(dist, "lade_eta_lab/static"),
  { recursive: true },
);
await cp(
  resolve(root, "lade_eta_lab/demo"),
  resolve(dist, "lade_eta_lab/demo"),
  { recursive: true },
);

for (const filename of [
  "summary.csv",
  "runs.csv",
  "timeline.csv",
  "predictions.csv",
  "experiment_config.json",
  "data_manifest.json",
  "modification_log.csv",
  "metrics_dictionary.json",
]) {
  await cp(
    resolve(root, "lade_eta_lab/results", filename),
    resolve(dist, "download-assets", filename),
  );
}
for (const [source, destination] of [
  ["README (1).md", "roadmap_readme.md"],
  ["roadmap-section.html", "roadmap-section.html"],
  ["roadmap-data.js", "roadmap-data.js"],
  ["user-report.html", "user-report.html"],
  ["render_videos.py", "render_videos.py"],
  ["review_layer.zip", "review_layer.zip"],
  ["render_videos (1).py", "render_videos_split.py"],
]) {
  await cp(resolve(root, source), resolve(dist, "download-assets", destination));
}
for (const filename of [
  "review_preview_alerts.png",
  "review_preview_data.png",
  "review_preview_decision.png",
  "PREVIEW_split_with_music_sample_data.mp4",
]) {
  await cp(resolve(root, filename), resolve(dist, "support", filename));
}
console.log("Built Cloudflare static assets in dist/.");
