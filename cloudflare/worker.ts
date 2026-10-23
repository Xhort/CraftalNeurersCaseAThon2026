import { DurableObject } from "cloudflare:workers";

interface Env {
  BACKEND: DurableObjectNamespace<Backend>;
  ASSETS: Fetcher;
}

type Decision = Record<string, unknown>;

const INACTIVITY_TIMEOUT_MS = 15 * 60 * 1000;
const DECISION_PREFIX = "decision:";
const DECISION_HEADERS = [
  "timestamp_utc",
  "package_id",
  "condition",
  "mitigated",
  "model_eta_minutes",
  "actual_minutes_demo_only",
  "action",
  "override_minutes",
  "reviewer",
  "review_seconds",
  "note",
];

function jsonResponse(value: unknown, status = 200): Response {
  return Response.json(value, {
    status,
    headers: { "cache-control": "no-store" },
  });
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export class Backend extends DurableObject<Env> {
  private starting: Promise<void> | undefined;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    const container = ctx.container!;
    if (container.running) {
      void ctx.blockConcurrencyWhile(() =>
        container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS),
      );
    }
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/api/decisions") {
      return this.listDecisions(url);
    }
    if (
      request.method === "GET" &&
      url.pathname === "/api/downloads/overrides.csv"
    ) {
      return this.downloadDecisions();
    }

    const response = await this.forwardToContainer(request);
    if (
      request.method === "POST" &&
      url.pathname === "/api/decisions" &&
      response.ok
    ) {
      const payload = (await response.json()) as {
        saved: boolean;
        decision: Decision;
      };
      const timestamp = String(payload.decision.timestamp_utc ?? new Date().toISOString());
      await this.ctx.storage.put(
        `${DECISION_PREFIX}${timestamp}:${crypto.randomUUID()}`,
        payload.decision,
      );
      return jsonResponse(payload, response.status);
    }
    return response;
  }

  private async listDecisions(url: URL): Promise<Response> {
    const requested = Number.parseInt(url.searchParams.get("limit") ?? "20", 10);
    const limit = Math.max(1, Math.min(Number.isFinite(requested) ? requested : 20, 200));
    const stored = await this.ctx.storage.list<Decision>({
      prefix: DECISION_PREFIX,
      reverse: true,
      limit,
    });
    return jsonResponse({ records: [...stored.values()] });
  }

  private async downloadDecisions(): Promise<Response> {
    const stored = await this.ctx.storage.list<Decision>({
      prefix: DECISION_PREFIX,
      reverse: true,
    });
    const rows = [
      DECISION_HEADERS.join(","),
      ...[...stored.values()].map((decision) =>
        DECISION_HEADERS.map((header) => csvCell(decision[header])).join(","),
      ),
    ];
    return new Response(`${rows.join("\n")}\n`, {
      headers: {
        "content-type": "text/csv; charset=utf-8",
        "content-disposition": 'attachment; filename="overrides.csv"',
        "cache-control": "no-store",
      },
    });
  }

  private async forwardToContainer(request: Request): Promise<Response> {
    this.starting ??= this.startAndWaitForPort().finally(() => {
      this.starting = undefined;
    });
    await this.starting;

    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    return this.ctx.container!.getTcpPort(8080).fetch(forwarded);
  }

  private async startAndWaitForPort(): Promise<void> {
    const container = this.ctx.container!;
    if (!container.running) {
      container.start({
        image: container.images.base,
        instance: "lite",
        enableInternet: false,
      });
    }
    await container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS);

    const port = container.getTcpPort(8080);
    let lastError: unknown;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      try {
        const response = await port.fetch("http://container/api/health", {
          signal: AbortSignal.timeout(1000),
        });
        await response.body?.cancel();
        if (!response.ok) throw new Error(`Health check returned ${response.status}`);
        return;
      } catch (error) {
        lastError = error;
        await scheduler.wait(200);
      }
    }
    throw new Error("FastAPI container did not become ready on port 8080", {
      cause: lastError,
    });
  }
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    const pathname = new URL(request.url).pathname;
    if (pathname === "/api" || pathname.startsWith("/api/")) {
      return env.BACKEND.getByName("antydata-backend").fetch(request);
    }
    return env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<Env>;
