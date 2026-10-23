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
const MODEL_FEATURES = [
  "region_code", "aoi_type_code", "accept_hour", "hour_sin", "hour_cos",
  "day_of_week", "distance_km", "courier_recent_accepts",
  "area_historical_eta", "courier_historical_eta",
] as const;
const UPLOAD_REQUIRED_FIELDS = [
  "region_code", "aoi_type_code", "accept_hour", "day_of_week", "distance_km",
  "courier_recent_accepts", "area_historical_eta", "courier_historical_eta",
] as const;
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

function requiredUploadNumber(row: any, field: string, index: number): number {
  const value = Number(row?.[field]);
  if (!Number.isFinite(value)) throw new Error(`Row ${index + 1}: ${field} must be a number.`);
  return value;
}

function validateUploadRows(body: any): any[] {
  if (!Array.isArray(body?.rows) || body.rows.length < 1 || body.rows.length > 50) {
    throw new Error("Provide between 1 and 50 prepared CSV rows.");
  }
  return body.rows.map((row: any, index: number) => {
    const values = Object.fromEntries(
      UPLOAD_REQUIRED_FIELDS.map((field) => [field, requiredUploadNumber(row, field, index)]),
    ) as Record<string, number>;
    const ranges: Record<string, [number, number]> = {
      region_code: [0, 100000], aoi_type_code: [0, 100000], accept_hour: [0, 24],
      day_of_week: [0, 6], distance_km: [0, 100], courier_recent_accepts: [0, 10000],
      area_historical_eta: [0, 360], courier_historical_eta: [0, 360],
    };
    for (const [field, [minimum, maximum]] of Object.entries(ranges)) {
      if (values[field] < minimum || values[field] > maximum
        || (field === "accept_hour" && values[field] === 24)) {
        throw new Error(`Row ${index + 1}: ${field} is outside the supported range.`);
      }
    }
    const actual = row.duration_minutes === null || row.duration_minutes === undefined
      || row.duration_minutes === "" ? null : Number(row.duration_minutes);
    if (actual !== null && (!Number.isFinite(actual) || actual < 1 || actual > 360)) {
      throw new Error(`Row ${index + 1}: duration_minutes must be between 1 and 360.`);
    }
    const hourRadians = 2 * Math.PI * values.accept_hour / 24;
    const clean = {
      ...values,
      hour_sin: Math.sin(hourRadians), hour_cos: Math.cos(hourRadians),
      data_age_days: 0, source_reliability: 1, low_quality_share: 0,
    };
    const degraded = {
      ...clean,
      distance_km: Math.min(100, clean.distance_km * 1.42),
      area_historical_eta: Math.min(360, clean.area_historical_eta + 10),
      source_reliability: 0.25, low_quality_share: 0.55,
    };
    return {
      package_id: String(row.package_id || `upload-${index + 1}`),
      city: row.city == null ? null : String(row.city).slice(0, 100),
      region_id: row.region_id == null ? null : String(row.region_id).slice(0, 100),
      actual, clean, degraded,
    };
  });
}

function reviewRisk(row: any, staleAfterDays = 7): number {
  const ageRisk = Math.min(1, Math.max(0, row.data_age_days / Math.max(staleAfterDays * 3, 1)));
  const sourceRisk = 1 - Math.min(1, Math.max(0.1, row.source_reliability));
  const volumeRisk = Math.min(1, Math.max(0, row.low_quality_share));
  return Math.min(1, Math.max(0, 0.4 * ageRisk + 0.4 * sourceRisk + 0.2 * volumeRisk));
}

function modelVector(row: any): number[] {
  return MODEL_FEATURES.map((field) => Number(row[field]));
}

function uploadedBatchResponse(rows: any[]): any {
  const degradedDistances = rows.map((row) => row.degraded.distance_km).sort((a, b) => a - b);
  const middle = Math.floor(degradedDistances.length / 2);
  const distanceMedian = degradedDistances.length % 2
    ? degradedDistances[middle]
    : (degradedDistances[middle - 1] + degradedDistances[middle]) / 2;
  const errors: Record<Scenario, number[]> = { clean: [], poisoned: [], safeguarded: [] };
  const results = rows.map((row) => {
    const modelRows: Record<Scenario, any> = {
      clean: row.clean,
      poisoned: row.degraded,
      safeguarded: {
        ...row.degraded,
        distance_km: row.degraded.source_reliability * row.degraded.distance_km
          + (1 - row.degraded.source_reliability) * distanceMedian,
        area_historical_eta: row.degraded.source_reliability * row.degraded.area_historical_eta
          + (1 - row.degraded.source_reliability) * EDGE_DATA.live.models.safeguarded.globalBaseline,
      },
    };
    const predictions: Record<string, any> = {};
    for (const scenario of SCENARIOS) {
      const model = EDGE_DATA.live.models[scenario];
      const prediction = evaluateModel(model, modelVector(modelRows[scenario]));
      const riskRow = scenario === "clean" ? row.clean : row.degraded;
      const risk = reviewRisk(riskRow, model.staleAfterDays);
      const halfWidth = model.residualQ90 * (1 + risk);
      const absoluteError = row.actual === null ? null : Math.abs(row.actual - prediction);
      if (absoluteError !== null) errors[scenario].push(absoluteError);
      predictions[scenario] = {
        prediction_minutes: rounded(prediction),
        interval: {
          low_minutes: rounded(Math.max(0, prediction - halfWidth)),
          high_minutes: rounded(prediction + halfWidth),
        },
        absolute_error_minutes: absoluteError === null ? null : rounded(absoluteError),
        review: {
          flagged: risk >= 0.35,
          reason: risk >= 0.35
            ? "location input came from a low-reliability feed; unreliable feed supplies 55% of recent inputs"
            : "inputs passed the configured reliability checks",
        },
      };
    }
    return {
      package_id: row.package_id, city: row.city, region_id: row.region_id,
      actual_duration_minutes_demo_only: row.actual, predictions,
    };
  });
  const mean = (values: number[]): number | null => values.length
    ? rounded(values.reduce((total, value) => total + value, 0) / values.length)
    : null;
  return {
    results,
    summary: {
      rows_scored: results.length,
      rows_with_outcomes: results.filter((row) => row.actual_duration_minutes_demo_only !== null).length,
      mae_minutes: {
        clean: mean(errors.clean), poisoned: mean(errors.poisoned), safeguarded: mean(errors.safeguarded),
      },
      safeguarded_review_rate: rounded(
        results.filter((row) => row.predictions.safeguarded.review.flagged).length / results.length, 4,
      ),
    },
    method: {
      input_format: "prepared acceptance-time feature rows",
      models: {
        clean: "reference_unmitigated.joblib", poisoned: "low_quality_unmitigated.joblib",
        safeguarded: "low_quality_mitigated.joblib",
      },
      trusted_input: "uploaded feature values with trusted-source metadata",
      degraded_input: {
        distance_multiplier: 1.42, area_history_added_minutes: 10,
        source_reliability: 0.25, low_quality_share: 0.55,
      },
      outcome_used_as_model_input: false,
      note: "The upload comparison uses the same three fitted artifacts. A transparent, deterministic low-quality profile is applied to uploaded rows because they were not members of the original seeded evaluation batch.",
    },
    computed_at_utc: new Date().toISOString(),
  };
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
  if (path === "/api/live-demo/upload-predict" && request.method === "POST") {
    let body: any;
    try {
      body = await parseJsonBody(request);
      return jsonResponse(uploadedBatchResponse(validateUploadRows(body)));
    } catch (error) {
      return errorResponse(422, error instanceof Error ? error.message : "Invalid upload rows.");
    }
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
