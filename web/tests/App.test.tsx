import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import App from "@/app/App";

// `<AuthProvider>` calls `/api/auth/me` on mount; the result drives
// the router's first render. Each test stubs `fetch` to control the
// branch under test.

const ORIGINAL_FETCH = globalThis.fetch;

function pathOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.pathname + input.search;
  return input.url;
}

function mockFetchOnce(handler: (input: RequestInfo | URL, init?: RequestInit) => Response) {
  globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    // The notification bell polls in the background; route it to an
    // empty array so it doesn't piggyback on the main handler.
    if (pathOf(input).startsWith("/api/notifications")) {
      return Promise.resolve(
        new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }),
      );
    }
    return Promise.resolve(handler(input, init));
  });
}

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  window.localStorage.clear();
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("<App /> router", () => {
  it("redirects an unauthenticated visit of `/` to `/login`", async () => {
    mockFetchOnce(() =>
      jsonResponse(401, { detail: { code: "auth.session.invalid", reason: "" } }),
    );

    render(<App />);

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent(/Sign in|로그인/);
    expect(window.location.pathname).toBe("/login");
  });

  it("renders the projects placeholder once `/me` resolves", async () => {
    window.history.replaceState({}, "", "/projects");
    mockFetchOnce(() =>
      jsonResponse(200, { user_id: "u1", email: "alice@example.com", display_name: null }),
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/Projects|프로젝트/);
    });
    // The header surfaces the signed-in email beside the sign-out
    // button. Both are present once `/me` resolves.
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sign out|로그아웃/ })).toBeInTheDocument();
  });

  it("shows the language switcher with two options", async () => {
    mockFetchOnce(() =>
      jsonResponse(401, { detail: { code: "auth.session.invalid", reason: "" } }),
    );

    render(<App />);

    // The header now hosts two combos: theme + language.
    // The language switcher is the one with an aria-label that
    // matches the locale label string ("Language" / "언어").
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: /Language|언어/ })).toBeInTheDocument();
    });
    const langSelect = screen.getByRole("combobox", { name: /Language|언어/ });
    expect(langSelect.querySelectorAll("option")).toHaveLength(2);
  });

  it("shows a friendly error banner when `/me` is non-401", async () => {
    mockFetchOnce(() => jsonResponse(503, { detail: { code: "server.down", reason: "boom" } }));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });
});
