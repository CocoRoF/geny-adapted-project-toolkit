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

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.clearAllMocks();
});

describe("<Login />", () => {
  it("submits the email and surfaces the magic link sent state", async () => {
    const sequence: Array<(input: RequestInfo | URL) => Response> = [
      () => jsonResponse(401, { detail: { code: "auth.session.invalid", reason: "" } }), // /me
      () => jsonResponse(200, { delivered: true }), // /_gapt/api/auth/magic-link
    ];
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const handler = sequence.shift();
      if (!handler) {
        const desc =
          typeof input === "string" ? input : input instanceof URL ? input.href : "request";
        throw new Error(`Unexpected fetch ${desc}`);
      }
      return Promise.resolve(handler(input));
    });

    render(
      <MemoryRouter>
        <I18nProvider>
          <AuthProvider>
            <Login />
          </AuthProvider>
        </I18nProvider>
      </MemoryRouter>,
    );

    const email = await screen.findByLabelText(/Email|이메일/);
    fireEvent.change(email, { target: { value: "alice@example.com" } });
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(screen.getByText(/Magic link sent|매직 링크가 전송/)).toBeInTheDocument();
    });
  });

  it("shows an inline error when the API rejects the email", async () => {
    const sequence: Array<(input: RequestInfo | URL) => Response> = [
      () => jsonResponse(401, { detail: { code: "auth.session.invalid", reason: "" } }), // /me
      () => jsonResponse(400, { detail: { code: "auth.email.invalid", reason: "bad email" } }),
    ];
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const handler = sequence.shift();
      if (!handler) {
        const desc =
          typeof input === "string" ? input : input instanceof URL ? input.href : "request";
        throw new Error(`Unexpected fetch ${desc}`);
      }
      return Promise.resolve(handler(input));
    });

    render(
      <MemoryRouter>
        <I18nProvider>
          <AuthProvider>
            <Login />
          </AuthProvider>
        </I18nProvider>
      </MemoryRouter>,
    );

    const email = await screen.findByLabelText(/Email|이메일/);
    fireEvent.change(email, { target: { value: "bad@example.com" } });
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("alert").textContent).toContain("auth.email.invalid");
  });
});
