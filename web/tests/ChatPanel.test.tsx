import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { ChatPanel } from "@/chat/ChatPanel";

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Route {
  match: (input: RequestInfo | URL, init?: RequestInit) => boolean;
  handler: () => Response;
}

function pathOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.pathname + input.search;
  return input.url;
}

function mockRoutes(routes: Route[]): void {
  globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const match = routes.find((r) => r.match(input, init));
    if (!match) {
      throw new Error(`Unexpected fetch ${init?.method ?? "GET"} ${pathOf(input)}`);
    }
    return Promise.resolve(match.handler());
  });
}

// Minimal EventSource stub — happy-dom doesn't provide one. We only
// exercise the open/close lifecycle; SSE event dispatching is left
// to the integration tests in M1-E4.
class StubEventSource {
  static CLOSED = 2;
  static OPEN = 1;
  static CONNECTING = 0;

  url: string;
  withCredentials: boolean;
  readyState = StubEventSource.OPEN;
  onopen: (() => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  private readonly listeners: Record<string, ((event: MessageEvent<string>) => void)[]> = {};

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = !!init?.withCredentials;
    // Open on the next microtask so the listener wiring lands first.
    queueMicrotask(() => {
      if (this.readyState !== StubEventSource.OPEN) return;
      this.onopen?.();
    });
  }

  addEventListener(kind: string, fn: (event: MessageEvent<string>) => void): void {
    (this.listeners[kind] ??= []).push(fn);
  }

  close(): void {
    this.readyState = StubEventSource.CLOSED;
  }

  // Helper for tests to inject an SSE event.
  emit(kind: string, data: object, id?: string): void {
    const event = {
      data: JSON.stringify(data),
      lastEventId: id ?? "",
      type: kind,
    } as MessageEvent<string>;
    for (const fn of this.listeners[kind] ?? []) fn(event);
  }
}

const ORIGINAL_EVENT_SOURCE = (globalThis as { EventSource?: typeof EventSource }).EventSource;

beforeEach(() => {
  (globalThis as unknown as { EventSource: unknown }).EventSource = StubEventSource;
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  (globalThis as unknown as { EventSource: unknown }).EventSource = ORIGINAL_EVENT_SOURCE;
  vi.clearAllMocks();
});

function renderChat() {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <ChatPanel projectId="p1" workspaceId="w1" />
      </I18nProvider>
    </MemoryRouter>,
  );
}

const SESSION = {
  id: "s1",
  project_id: "p1",
  workspace_id: "w1",
  user_id: "u1",
  env_manifest_id: "gapt_default",
  status: "active",
  cost_usd: 0,
  input_tokens: 0,
  output_tokens: 0,
  last_active_at: "2026-05-23T00:00:00Z",
  created_at: "2026-05-23T00:00:00Z",
};

describe("<ChatPanel />", () => {
  it("shows the empty-state with a Start session button", async () => {
    mockRoutes([
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/projects/p1/sessions"),
        handler: () => jsonResponse(200, []),
      },
    ]);

    renderChat();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Start session|세션 시작/ })).toBeInTheDocument();
    });
  });

  it("starts a session and then renders the chat composer", async () => {
    mockRoutes([
      {
        match: (input, init) =>
          pathOf(input).startsWith("/_gapt/api/projects/p1/sessions") &&
          (init?.method ?? "GET") === "GET",
        handler: () => jsonResponse(200, []),
      },
      {
        match: (input, init) =>
          pathOf(input) === "/_gapt/api/projects/p1/sessions" && init?.method === "POST",
        handler: () => jsonResponse(201, SESSION),
      },
    ]);

    renderChat();

    const startBtn = await screen.findByRole("button", { name: /Start session|세션 시작/ });
    fireEvent.click(startBtn);

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Ask the agent|에이전트에게 질문/)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Send|전송/ })).toBeInTheDocument();
    expect(screen.getByTestId("chat-cost")).toBeInTheDocument();
  });

  it("auto-attaches to an existing active session on mount", async () => {
    mockRoutes([
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/projects/p1/sessions"),
        handler: () => jsonResponse(200, [SESSION]),
      },
    ]);

    renderChat();

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Ask the agent|에이전트에게 질문/)).toBeInTheDocument();
    });
  });
});
