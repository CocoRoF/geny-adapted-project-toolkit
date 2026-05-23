import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, apiGet, apiPost } from "@/api/client";

const ORIGINAL_FETCH = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.clearAllMocks();
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch", () => {
  it("sends JSON when given a `json` body", async () => {
    let captured: RequestInit | null = null;
    globalThis.fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      captured = init ?? null;
      return Promise.resolve(jsonResponse(200, { ok: true }));
    });

    const out = await apiPost<{ ok: boolean }>("/api/x", { hello: "world" });
    expect(out).toEqual({ ok: true });
    expect(captured).not.toBeNull();
    expect(captured!.method).toBe("POST");
    expect(captured!.body).toBe(JSON.stringify({ hello: "world" }));
    const headers = new Headers(captured!.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("returns undefined on 204", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })));
    await expect(apiFetch<void>("/api/y", { method: "POST" })).resolves.toBeUndefined();
  });

  it("normalises FastAPI error envelopes into ApiError", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        jsonResponse(403, { detail: { code: "project.forbidden", reason: "no access" } }),
      ),
    );

    await expect(apiGet("/api/forbidden")).rejects.toMatchObject({
      status: 403,
      code: "project.forbidden",
      reason: "no access",
    });
  });

  it("synthesises a code from the status when the body isn't JSON", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(new Response("oops", { status: 500 })));

    await expect(apiGet("/api/boom")).rejects.toBeInstanceOf(ApiError);
    await expect(apiGet("/api/boom")).rejects.toMatchObject({
      status: 500,
      code: "http.500",
    });
  });
});
