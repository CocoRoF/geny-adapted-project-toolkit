import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// Minimal EventSource stub — happy-dom doesn't provide one. We drive
// the open/close lifecycle *and* dispatch real SSE frames into the
// hook's `addEventListener` handlers so tests can exercise the
// event-rendering path end to end (see ChatPanelStream.test.tsx for
// the live-stream assertions). Each constructed instance registers
// itself so a test can grab the one bound to the active session.
class StubEventSource {
  static CLOSED = 2;
  static OPEN = 1;
  static CONNECTING = 0;

  /** Every instance the component-under-test constructs, newest last.
   *  Tests reach in via `StubEventSource.last()` to drive frames. */
  static instances: StubEventSource[] = [];

  static last(): StubEventSource {
    const inst = StubEventSource.instances.at(-1);
    if (!inst) throw new Error("no StubEventSource was constructed yet");
    return inst;
  }

  static reset(): void {
    StubEventSource.instances = [];
  }

  url: string;
  withCredentials: boolean;
  readyState = StubEventSource.OPEN;
  onopen: (() => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  private readonly listeners: Record<string, ((event: MessageEvent<string>) => void)[]> = {};

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = !!init?.withCredentials;
    StubEventSource.instances.push(this);
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

  // Inject an SSE frame: builds a MessageEvent-shaped object and runs
  // every handler the hook registered for `kind` via addEventListener.
  // `id` maps to `lastEventId` — the hook reads `Number(lastEventId)`
  // as the server-assigned `seq` (used for ordering + dedup).
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
  StubEventSource.reset();
  (globalThis as unknown as { EventSource: unknown }).EventSource = StubEventSource;
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  (globalThis as unknown as { EventSource: unknown }).EventSource = ORIGINAL_EVENT_SOURCE;
  StubEventSource.reset();
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

  // ── live SSE event rendering ─────────────────────────────────────
  // Mount with an already-active session, grab the StubEventSource the
  // hook constructed, and drive real SSE frames through it. The bubbles
  // these produce are the actual `useSessionStream` → `ChatPanel`
  // render path, not a mock of it.

  /** Auto-attach to SESSION and wait until the stream is wired (composer
   *  visible + an EventSource constructed), then return its stub. */
  async function startStreamingSession(): Promise<StubEventSource> {
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
    // The session effect runs `new EventSource(...)`. Wait for it to land
    // (it can trail the composer by a microtask) before returning.
    await waitFor(() => {
      expect(StubEventSource.instances.length).toBeGreaterThan(0);
    });
    return StubEventSource.last();
  }

  it("renders an assistant bubble from a streamed text event", async () => {
    const es = await startStreamingSession();

    // A turn carries the user's prompt (`user_message`) then the
    // assistant's reply (`text`). seqs are server-assigned + monotonic.
    act(() => {
      es.emit("user_message", { text: "hello from the user" }, "1");
      es.emit("text", { text: "hello from the assistant", role: "assistant" }, "2");
    });

    // The user prompt renders right-aligned (user_message bubble) …
    await waitFor(() => {
      expect(screen.getByText("hello from the user")).toBeInTheDocument();
    });
    // … and the assistant reply renders left-aligned (markdown bubble).
    const assistantBubble = await screen.findByText("hello from the assistant");
    expect(assistantBubble).toBeInTheDocument();
    const bubbleRoot = assistantBubble.closest('[data-event-kind="text"]');
    expect(bubbleRoot).not.toBeNull();
    expect(bubbleRoot).toHaveAttribute("data-role", "assistant");
  });

  it("dedups two frames that share the same numeric lastEventId (seq)", async () => {
    const es = await startStreamingSession();

    // The server replays a turn's history from the top on every
    // reconnect; a frame the client already committed arrives again
    // with the SAME seq. The hook keys on seq and must drop the repeat.
    // We give the duplicate a DIFFERENT body so a dedup miss can't hide
    // behind the assistant-text merge (which would *concatenate* two
    // identical frames into one bubble): if seq 7 leaked twice we'd see
    // the second body too.
    act(() => {
      es.emit("user_message", { text: "kick off the turn" }, "6");
      es.emit("text", { text: "first commit of seq 7", role: "assistant" }, "7");
      es.emit("text", { text: "SHOULD NOT APPEAR (replay of seq 7)", role: "assistant" }, "7");
    });

    await waitFor(() => {
      expect(screen.getByText("first commit of seq 7")).toBeInTheDocument();
    });
    // The replayed frame with the already-seen seq must never render.
    expect(screen.queryByText(/SHOULD NOT APPEAR/)).toBeNull();
    // Hold across a flush to prove the duplicate doesn't land late.
    await Promise.resolve();
    expect(screen.queryByText(/SHOULD NOT APPEAR/)).toBeNull();
    expect(screen.getAllByText("first commit of seq 7")).toHaveLength(1);
  });
});
