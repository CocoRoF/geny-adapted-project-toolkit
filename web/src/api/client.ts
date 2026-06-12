/** Lightweight fetch client.
 *
 * Centralised so:
 *  - The base URL flips between dev (Vite proxy) and prod (relative).
 *  - Every response funnels through one error normaliser that surfaces
 *    `exec.*.*` codes in a stable shape. Components can rely on
 *    `err.code` / `err.reason` without inspecting `await resp.json()`
 *    themselves.
 *  - SSE streams (Cycle 3.8) read from the same base + cookie auth.
 */

const DEFAULT_BASE = "/";

export interface ApiErrorBody {
  code: string;
  reason: string;
  // Phase N.3 — handlers may attach extra structured context (e.g.
  // `session.budget_exhausted` reports `cost_usd` + `cost_budget_usd`)
  // so the UI can render an actionable banner instead of a generic
  // red blob. Free-form so we don't have to extend the interface
  // each time a new error code shows up.
  [extra: string]: unknown;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly reason: string,
    /** Phase N.3 — full `detail` object from the server. Lets the
     *  UI fish out structured context (cost / cap / etc.) without
     *  parsing the reason string. */
    public readonly details: Record<string, unknown> = {},
  ) {
    super(`${code}: ${reason}`);
    this.name = "ApiError";
  }
}

function resolveBase(): string {
  if (typeof import.meta !== "undefined") {
    const fromEnv = (import.meta as unknown as { env?: Record<string, string | undefined> }).env
      ?.VITE_API_BASE;
    if (fromEnv && fromEnv.length > 0) return fromEnv.replace(/\/$/, "") + "/";
  }
  return DEFAULT_BASE;
}

function url(path: string): string {
  const base = resolveBase();
  return base.replace(/\/$/, "") + (path.startsWith("/") ? path : `/${path}`);
}

async function parseError(resp: Response): Promise<ApiError> {
  let code = `http.${resp.status}`;
  let reason = resp.statusText || "request failed";
  let details: Record<string, unknown> = {};
  try {
    const body = (await resp.json()) as { detail?: ApiErrorBody | string };
    if (body && typeof body.detail === "object" && body.detail !== null) {
      code = body.detail.code ?? code;
      reason = body.detail.reason ?? reason;
      details = body.detail;
    } else if (typeof body?.detail === "string") {
      reason = body.detail;
    }
  } catch {
    // Body wasn't JSON — keep the synthesised code/reason.
  }
  return new ApiError(resp.status, code, reason, details);
}

interface RequestInitJson extends Omit<RequestInit, "body"> {
  json?: unknown;
}

export async function apiFetch<T>(path: string, init?: RequestInitJson): Promise<T> {
  const { json, headers, ...rest } = init ?? {};
  const finalHeaders = new Headers(headers);
  let body: BodyInit | undefined;
  if (json !== undefined) {
    finalHeaders.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }
  if (!finalHeaders.has("Accept")) {
    finalHeaders.set("Accept", "application/json");
  }
  const requestInit: RequestInit = {
    credentials: "include",
    ...rest,
    headers: finalHeaders,
  };
  if (body !== undefined) {
    requestInit.body = body;
  }
  const resp = await fetch(url(path), requestInit);
  if (!resp.ok) {
    throw await parseError(resp);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

// Re-exported helpers a route can call without importing the whole client.
export const apiGet = <T>(path: string) => apiFetch<T>(path, { method: "GET" });
export const apiPost = <T>(path: string, json?: unknown) =>
  apiFetch<T>(path, { method: "POST", json });
export const apiDelete = <T>(path: string) => apiFetch<T>(path, { method: "DELETE" });
export const apiPatch = <T>(path: string, json?: unknown) =>
  apiFetch<T>(path, { method: "PATCH", json });
