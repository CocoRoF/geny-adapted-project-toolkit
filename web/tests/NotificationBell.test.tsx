import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { NotificationBell } from "@/notifications/NotificationBell";

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.clearAllMocks();
  vi.useRealTimers();
});

function renderBell() {
  return render(
    <I18nProvider>
      <NotificationBell />
    </I18nProvider>,
  );
}

const SAMPLE = [
  {
    id: "n1",
    kind: "deploy.success",
    title: "Deploy succeeded",
    body: "prod is live",
    actor_id: "u1",
    project_id: "p1",
    workspace_id: null,
    severity: "info",
    ts: Date.now() / 1000,
    details: {},
  },
  {
    id: "n2",
    kind: "policy.denied",
    title: "Policy denied",
    body: "deploy.prod requires 2FA",
    actor_id: "u1",
    project_id: "p1",
    workspace_id: null,
    severity: "error",
    ts: Date.now() / 1000 - 600,
    details: {},
  },
];

describe("<NotificationBell />", () => {
  it("fetches and renders unread badge then list on open", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(jsonResponse(200, SAMPLE)));

    renderBell();

    await waitFor(() => {
      expect(screen.getByTestId("notification-badge")).toHaveTextContent("2");
    });

    fireEvent.click(screen.getByTestId("notification-bell"));

    await waitFor(() => {
      expect(screen.getByTestId("notification-dropdown")).toBeInTheDocument();
    });
    expect(screen.getAllByTestId("notification-item")).toHaveLength(2);
    expect(screen.getByText("Deploy succeeded")).toBeInTheDocument();
    expect(screen.getByText("Policy denied")).toBeInTheDocument();
  });

  it("shows empty state when the feed is empty", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(jsonResponse(200, [])));

    renderBell();
    fireEvent.click(await screen.findByTestId("notification-bell"));
    await waitFor(() => {
      expect(screen.getByText(/No notifications yet|아직 알림이 없습니다/)).toBeInTheDocument();
    });
  });

  it("surfaces an API error", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(jsonResponse(500, { detail: { code: "server.boom", reason: "db" } })),
    );

    renderBell();
    fireEvent.click(await screen.findByTestId("notification-bell"));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("server.boom");
    });
  });
});
