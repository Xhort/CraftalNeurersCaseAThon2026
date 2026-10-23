import { cp, mkdir, rm } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const dist = resolve(root, "dist");

await rm(dist, { recursive: true, force: true });
await mkdir(resolve(dist, "lade_eta_lab"), { recursive: true });
await cp(resolve(root, "index.html"), resolve(dist, "index.html"));
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

console.log("Built Cloudflare static assets in dist/.");
