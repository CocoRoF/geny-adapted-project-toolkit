import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { Login } from "@/routes/Login";
import { AuthProvider } from "@/app/providers/AuthProvider";
import { I18nProvider } from "@/app/providers/I18nProvider";

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function emptyResponse(status: number): Response {
  return new Response(null, { status });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.clearAllMocks();
});

function renderLogin() {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <AuthProvider>
          <Login />
        </AuthProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

/** Single-admin id/password sign-in (MinIO/Jenkins style) — the
 * magic-link flow this file used to cover no longer exists. */
describe("<Login />", () => {
  it("submits id+password and transitions to signed-in", async () => {
    const calls: string[] = [];
    const sequence: Array<() => Response> = [
      // AuthProvider boot probe.
      () => jsonResponse(401, { detail: { code: "auth.session.invalid", reason: "" } }),
      // POST /_gapt/api/auth/login — sets the session cookie.
      () => emptyResponse(204),
      // refresh() → /me now resolves.
      () => jsonResponse(200, { id: "admin" }),
    ];
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      calls.push(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
      const handler = sequence.shift();
      if (!handler) throw new Error(`Unexpected fetch ${calls.at(-1)}`);
      return Promise.resolve(handler());
    });

    renderLogin();

    fireEvent.change(await screen.findByLabelText(/ID|아이디/), {
      target: { value: "admin" },
    });
    fireEvent.change(screen.getByLabelText(/Password|비밀번호/), {
      target: { value: "admin" },
    });
    fireEvent.click(screen.getByRole("button"));

    // Signed-in → the Login screen unmounts its form (returns null
    // and navigates away).
    await waitFor(() => {
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });
    expect(calls.some((u) => u.includes("/_gapt/api/auth/login"))).toBe(true);
  });

  it("shows an inline error on invalid credentials", async () => {
    const sequence: Array<() => Response> = [
      () => jsonResponse(401, { detail: { code: "auth.session.invalid", reason: "" } }),
      () =>
        jsonResponse(401, {
          detail: { code: "auth.invalid_credentials", reason: "bad id/pw" },
        }),
    ];
    globalThis.fetch = vi.fn(() => {
      const handler = sequence.shift();
      if (!handler) throw new Error("Unexpected fetch");
      return Promise.resolve(handler());
    });

    renderLogin();

    fireEvent.change(await screen.findByLabelText(/ID|아이디/), {
      target: { value: "admin" },
    });
    fireEvent.change(screen.getByLabelText(/Password|비밀번호/), {
      target: { value: "wrong" },
    });
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("alert").textContent).toMatch(
      /Invalid ID or password|아이디 또는 비밀번호/,
    );
  });
});
