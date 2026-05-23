import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { AuditPanel } from "@/audit/AuditPanel";

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pathOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.pathname + input.search;
  return input.url;
}

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.clearAllMocks();
});

function renderPanel() {
  return render(
    <I18nProvider>
      <AuditPanel projectId="p1" />
    </I18nProvider>,
  );
}

const SAMPLE = [
  {
    id: "e1",
    ts: "2026-05-23T10:00:00Z",
    actor_type: "user",
    actor_id: "u1",
    scope: { project_id: "p1" },
    action: "project.create",
    subject: {},
    outcome: "ok",
    duration_ms: 12,
    exec_code: null,
    payload: {},
  },
  {
    id: "e2",
    ts: "2026-05-23T11:00:00Z",
    actor_type: "agent_session",
    actor_id: "s1",
    scope: { project_id: "p1" },
    action: "agent.tool_failure",
    subject: { tool_name: "gapt_edit" },
    outcome: "error",
    duration_ms: 5,
    exec_code: "exec.tool.access_denied",
    payload: {},
  },
];

describe("<AuditPanel />", () => {
  it("renders the table after fetching audit rows", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(jsonResponse(200, SAMPLE)));

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("audit-table")).toBeInTheDocument();
    });
    expect(screen.getByText("project.create")).toBeInTheDocument();
    expect(screen.getByText("agent.tool_failure")).toBeInTheDocument();
    expect(screen.getByText("exec.tool.access_denied")).toBeInTheDocument();
  });

  it("sends the filter prefix in the request", async () => {
    const seen: string[] = [];
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      seen.push(pathOf(input));
      return Promise.resolve(jsonResponse(200, []));
    });

    renderPanel();
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));

    fireEvent.change(screen.getByLabelText(/Action prefix|액션 접두사/), {
      target: { value: "agent." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Refresh|새로고침/ }));

    await waitFor(() => {
      expect(seen.some((p) => p.includes("action_prefix=agent."))).toBe(true);
    });
  });

  it("shows an empty-state when there are no entries", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(jsonResponse(200, [])));

    renderPanel();

    await waitFor(() => {
      expect(
        screen.getByText(
          /No audit events match this filter|이 필터에 일치하는 감사 이벤트가 없습니다/,
        ),
      ).toBeInTheDocument();
    });
  });

  it("surfaces an API error inline", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(jsonResponse(500, { detail: { code: "server.boom", reason: "db" } })),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("server.boom");
    });
  });

  it("exposes CSV and JSONL export anchors that carry the current filters", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(jsonResponse(200, SAMPLE)));

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("audit-table")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Action prefix|액션 접두사/), {
      target: { value: "agent." },
    });

    const csv = screen.getByTestId("audit-export-csv");
    const jsonl = screen.getByTestId("audit-export-jsonl");
    expect(csv.getAttribute("href")).toContain("/api/projects/p1/audit/export?");
    expect(csv.getAttribute("href")).toContain("format=csv");
    expect(csv.getAttribute("href")).toContain("action_prefix=agent.");
    expect(jsonl.getAttribute("href")).toContain("format=jsonl");
    expect(jsonl.getAttribute("href")).toContain("action_prefix=agent.");
  });

  it("paginates with Load more when a full page comes back", async () => {
    const page1 = Array.from({ length: 100 }, (_, i) => ({
      ...SAMPLE[0],
      id: `e${i}`,
      action: `bulk.${i}`,
    }));
    const page2 = [{ ...SAMPLE[0], id: "tail", action: "bulk.tail" }];
    const seen: string[] = [];
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const p = pathOf(input);
      seen.push(p);
      if (p.includes("offset=100")) return Promise.resolve(jsonResponse(200, page2));
      return Promise.resolve(jsonResponse(200, page1));
    });

    renderPanel();

    const loadMore = await screen.findByTestId("audit-load-more");
    fireEvent.click(loadMore);

    await waitFor(() => {
      expect(screen.getByText("bulk.tail")).toBeInTheDocument();
    });
    expect(seen.some((p) => p.includes("offset=100"))).toBe(true);
    // Second page (< PAGE_SIZE) collapses the Load more button.
    expect(screen.queryByTestId("audit-load-more")).not.toBeInTheDocument();
  });
});
