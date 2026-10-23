import { DurableObject } from "cloudflare:workers";
import { EDGE_DATA } from "./generated-data";

interface Env {
  DECISIONS: DurableObjectNamespace<DecisionStore>;
  ASSETS: Fetcher;
}

type Decision = Record<string, unknown>;
type Scenario = "clean" | "poisoned" | "safeguarded";

const DECISION_PREFIX = "decision:";
const DECISION_HEADERS = [
  "timestamp_utc", "package_id", "condition", "mitigated",
  "model_eta_minutes", "actual_minutes_demo_only", "action",
  "override_minutes", "reviewer", "review_seconds", "note",
];
const CONDITIONS = new Set(["reference", "stale", "low_quality", "combined"]);
const SCENARIOS = new Set<Scenario>(["clean", "poisoned", "safeguarded"]);
const DOWNLOADS: Record<string, string> = {
  "summary.csv": "text/csv; charset=utf-8",
  "runs.csv": "text/csv; charset=utf-8",
  "timeline.csv": "text/csv; charset=utf-8",
  "predictions.csv": "text/csv; charset=utf-8",
  "experiment_config.json": "application/json; charset=utf-8",
  "data_manifest.json": "application/json; charset=utf-8",
  "modification_log.csv": "text/csv; charset=utf-8",
  "metrics_dictionary.json": "application/json; charset=utf-8",
  "fallback_demo.mp4": "video/mp4",
};

function apiHeaders(contentType = "application/json; charset=utf-8"): HeadersInit {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type",
    "cache-control": "no-store",
    "content-type": contentType,
  };
}

function jsonResponse(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: apiHeaders() });
}

function errorResponse(status: number, detail: string): Response {
  return jsonResponse({ detail }, status);
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function parseBoolean(value: string | null, fallback: boolean): boolean {
  return value === null ? fallback : value.toLowerCase() === "true";
}

function rounded(value: number, digits = 1): number {
  const scale = 10 ** digits;
  return Math.round((value + Number.EPSILON) * scale) / scale;
}

function evaluateModel(model: any, vector: readonly (number | null)[]): number {
  const values = vector.map((value, index) =>
    value === null || !Number.isFinite(value) ? model.imputer[index] : value,
  );
  let prediction = model.baseline;
  for (const tree of model.trees) {
    let nodeIndex = 0;
    while (true) {
      const node = tree[nodeIndex];
      const [feature, threshold, missingLeft, left, right, leafValue, isLeaf] = node;
      if (isLeaf) {
        prediction += leafValue;
        break;
      }
      const value = values[feature];
      nodeIndex = !Number.isFinite(value)
        ? (missingLeft ? left : right)
        : (value <= threshold ? left : right);
    }
  }
  return prediction;
}

function livePredictions(record: any): Record<Scenario, number> {
  const result = {} as Record<Scenario, number>;
  for (const scenario of SCENARIOS) {
    result[scenario] = evaluateModel(EDGE_DATA.live.models[scenario], record.vectors[scenario]);
  }
  return result;
}

function casePayload(condition: string, mitigated: boolean): any {
  return (EDGE_DATA.evidence.cases as Record<string, any>)[`${condition}:${mitigated}`];
}

function caseRecords(condition: string, mitigated: boolean, flaggedOnly: boolean): any[] {
  const records = casePayload(condition, mitigated).records as any[];
  if (!flaggedOnly || !records.some((record) => record.flagged_for_review)) return records;
  return records.filter((record) => record.flagged_for_review);
}

async function parseJsonBody(request: Request): Promise<any> {
  if (!(request.headers.get("content-type") ?? "").includes("application/json")) {
    throw new Error("Send a JSON request body.");
  }
  return request.json();
}

export class DecisionStore extends DurableObject<Env> {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/api/decisions") {
      const requested = Number.parseInt(url.searchParams.get("limit") ?? "20", 10);
      const limit = Math.max(1, Math.min(Number.isFinite(requested) ? requested : 20, 200));
      const stored = await this.ctx.storage.list<Decision>({
        prefix: DECISION_PREFIX, reverse: true, limit,
      });
      return jsonResponse({ records: [...stored.values()] });
    }
    if (request.method === "GET" && url.pathname === "/api/downloads/overrides.csv") {
      const stored = await this.ctx.storage.list<Decision>({
        prefix: DECISION_PREFIX, reverse: true,
      });
      const rows = [
        DECISION_HEADERS.join(","),
        ...[...stored.values()].map((decision) =>
          DECISION_HEADERS.map((header) => csvCell(decision[header])).join(","),
        ),
      ];
      return new Response(`${rows.join("\n")}\n`, {
        headers: {
          ...apiHeaders("text/csv; charset=utf-8"),
          "content-disposition": 'attachment; filename="overrides.csv"',
        },
      });
    }
    if (request.method !== "POST" || url.pathname !== "/api/decisions") {
      return errorResponse(404, "API route not found.");
    }

    let body: any;
    try {
      body = await parseJsonBody(request);
    } catch (error) {
      return errorResponse(400, error instanceof Error ? error.message : "Invalid JSON body.");
    }
    const packageId = String(body.package_id ?? "");
    const condition = String(body.condition ?? "");
    if (!CONDITIONS.has(condition) || typeof body.mitigated !== "boolean") {
      return errorResponse(422, "A valid condition and mitigated value are required.");
    }
    const mitigated = body.mitigated;
    const action = String(body.action ?? "");
    const reviewer = String(body.reviewer ?? "Demo dispatcher").trim();
    const matched = (casePayload(condition, mitigated).records as any[])
      .find((record) => String(record.package_id) === packageId);
    if (!matched) return errorResponse(404, "The selected case was not found in the fixed evaluation set.");
    if (!["accept", "override", "escalate"].includes(action)) {
      return errorResponse(422, "Action must be accept, override, or escalate.");
    }
    const override = body.override_minutes === null || body.override_minutes === undefined
      ? null : Number(body.override_minutes);
    if (action === "override" && (!Number.isFinite(override) || override < 1 || override > 360)) {
      return errorResponse(422, "A valid override_minutes value is required for an override.");
    }
    if (action !== "override" && override !== null) {
      return errorResponse(422, "override_minutes is only valid for an override.");
    }
    if (!reviewer || reviewer.length > 80) return errorResponse(422, "Reviewer is required.");
    const reviewSeconds = body.review_seconds === null || body.review_seconds === undefined
      ? null : Number(body.review_seconds);
    if (reviewSeconds !== null
      && (!Number.isFinite(reviewSeconds) || reviewSeconds < 0 || reviewSeconds > 3600)) {
      return errorResponse(422, "review_seconds must be between 0 and 3600.");
    }
    const note = String(body.note ?? "");
    if (note.length > 500) return errorResponse(422, "Note must be 500 characters or fewer.");

    const entry: Decision = {
      timestamp_utc: new Date().toISOString(),
      package_id: packageId,
      condition,
      mitigated,
      model_eta_minutes: rounded(Number(matched.prediction_minutes), 3),
      actual_minutes_demo_only: rounded(Number(matched.duration_minutes), 3),
      action,
      override_minutes: action === "override" ? override : null,
      reviewer,
      review_seconds: reviewSeconds,
      note,
    };
    await this.ctx.storage.put(
      `${DECISION_PREFIX}${entry.timestamp_utc}:${crypto.randomUUID()}`, entry,
    );
    return jsonResponse({ saved: true, decision: entry }, 201);
  }
}

async function downloadResponse(request: Request, env: Env, filename: string): Promise<Response> {
  if (filename === "overrides.csv") {
    return env.DECISIONS.getByName("antydata-decisions").fetch(request);
  }
  const contentType = DOWNLOADS[filename];
  if (!contentType) return errorResponse(404, "Download not found.");
  const assetUrl = new URL(`/download-assets/${filename}`, request.url);
  const asset = await env.ASSETS.fetch(new Request(assetUrl, { method: "GET" }));
  if (!asset.ok) return errorResponse(404, "Download not found.");
  const headers = new Headers(asset.headers);
  headers.set("content-type", contentType);
  headers.set("content-disposition", `attachment; filename="${filename}"`);
  headers.set("cache-control", "public, max-age=300");
  return new Response(asset.body, { status: asset.status, headers });
}

async function handleApi(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: apiHeaders() });

  if (path === "/api/health" && request.method === "GET") {
    return jsonResponse({
      status: "ok", service: "lade-eta-reliability-lab", runtime: "cloudflare-workers",
      version: "2.0.0", utc_time: new Date().toISOString(), results_ready: true,
      model_inference_ready: true,
    });
  }
  if (path === "/api/overview" && request.method === "GET") {
    return jsonResponse(EDGE_DATA.evidence.overview);
  }
  if (path === "/api/comparison" && request.method === "GET") {
    return jsonResponse(EDGE_DATA.evidence.comparison);
  }
  if (path === "/api/timeline" && request.method === "GET") {
    const condition = url.searchParams.get("condition") ?? "combined";
    const mitigated = parseBoolean(url.searchParams.get("mitigated"), false);
    if (!CONDITIONS.has(condition)) return errorResponse(422, "Unknown experiment condition.");
    return jsonResponse((EDGE_DATA.evidence.timelines as Record<string, unknown>)[`${condition}:${mitigated}`]);
  }
  if (path === "/api/cases" && request.method === "GET") {
    const condition = url.searchParams.get("condition") ?? "combined";
    const mitigated = parseBoolean(url.searchParams.get("mitigated"), true);
    const flaggedOnly = parseBoolean(url.searchParams.get("flagged_only"), true);
    const rawLimit = Number.parseInt(url.searchParams.get("limit") ?? "80", 10);
    const limit = Math.max(1, Math.min(Number.isFinite(rawLimit) ? rawLimit : 80, 300));
    if (!CONDITIONS.has(condition)) return errorResponse(422, "Unknown experiment condition.");
    const records = caseRecords(condition, mitigated, flaggedOnly);
    return jsonResponse({ total: Math.min(limit, records.length), records: records.slice(0, limit) });
  }
  if (path === "/api/live-demo/cases" && request.method === "GET") {
    return jsonResponse(EDGE_DATA.live.cases);
  }
  if (path === "/api/live-demo/predict" && request.method === "POST") {
    let body: any;
    try {
      body = await parseJsonBody(request);
    } catch (error) {
      return errorResponse(400, error instanceof Error ? error.message : "Invalid JSON body.");
    }
    const packageId = String(body.package_id ?? "");
    const scenario = String(body.scenario ?? "") as Scenario;
    const record = (EDGE_DATA.live.records as Record<string, any>)[packageId];
    if (!record) return errorResponse(404, "Choose one of the published live-demo deliveries.");
    if (!SCENARIOS.has(scenario)) return errorResponse(422, "Unknown live-demo scenario.");

    const predictions = livePredictions(record);
    const response = structuredClone(record.templates[scenario]);
    response.prediction_minutes = rounded(predictions[scenario]);
    response.comparison.clean_prediction_minutes = rounded(predictions.clean);
    response.comparison.poisoned_prediction_minutes = rounded(predictions.poisoned);
    response.comparison.safeguarded_prediction_minutes = rounded(predictions.safeguarded);
    response.comparison.poisoning_shift_minutes = rounded(predictions.poisoned - predictions.clean);
    response.comparison.safeguard_shift_from_poisoned_minutes = rounded(predictions.safeguarded - predictions.poisoned);
    response.comparison.safeguard_recovery_toward_clean_minutes = rounded(
      Math.abs(predictions.poisoned - predictions.clean)
        - Math.abs(predictions.safeguarded - predictions.clean),
    );
    response.model.runtime = "Cloudflare Worker tree evaluator";
    response.model.prediction_computed_live_on_request = true;
    response.computed_at_utc = new Date().toISOString();
    return jsonResponse(response);
  }
  const outcomeMatch = path.match(/^\/api\/live-demo\/outcome\/([^/]+)$/);
  if (outcomeMatch && request.method === "GET") {
    const packageId = decodeURIComponent(outcomeMatch[1]);
    const record = (EDGE_DATA.live.records as Record<string, any>)[packageId];
    return record ? jsonResponse(record.outcome)
      : errorResponse(404, "Choose one of the published live-demo deliveries.");
  }
  if (path === "/api/decisions" || path === "/api/downloads/overrides.csv") {
    return env.DECISIONS.getByName("antydata-decisions").fetch(request);
  }
  if (path === "/api/docs" && request.method === "GET") {
    return jsonResponse(EDGE_DATA.evidence.docs);
  }
  const docMatch = path.match(/^\/api\/docs\/([^/]+)$/);
  if (docMatch && request.method === "GET") {
    const id = decodeURIComponent(docMatch[1]);
    const markdown = (EDGE_DATA.evidence.docContent as Record<string, string>)[id];
    return markdown === undefined ? errorResponse(404, "Documentation page not found.")
      : jsonResponse({ id, markdown });
  }
  const downloadMatch = path.match(/^\/api\/downloads\/([^/]+)$/);
  if (downloadMatch && request.method === "GET") {
    return downloadResponse(request, env, decodeURIComponent(downloadMatch[1]));
  }
  if (path === "/api/experiments/preview" && request.method === "POST") {
    return errorResponse(501,
      "The online demo serves the measured competition experiment. Run local Python for new synthetic retraining previews.");
  }
  return errorResponse(404, "API route not found.");
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const pathname = new URL(request.url).pathname;
    return pathname === "/api" || pathname.startsWith("/api/")
      ? handleApi(request, env)
      : env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<Env>;
