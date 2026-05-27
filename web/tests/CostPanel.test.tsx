import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { CostPanel } from "@/cost/CostPanel";

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
      <CostPanel />
    </I18nProvider>,
  );
}

const SUMMARY = {
  rows: [
    {
      project_id: "p1",
      project_slug: "demo",
      project_display_name: "Demo",
      org_id: "o1",
      cost_usd: 1.25,
      input_tokens: 200,
      output_tokens: 50,
      session_count: 3,
    },
    {
      project_id: "p2",
      project_slug: "experiment",
      project_display_name: "Experiment",
      org_id: "o1",
      cost_usd: 0.5,
      input_tokens: 100,
      output_tokens: 20,
      session_count: 1,
    },
  ],
  total_cost_usd: 1.75,
  total_input_tokens: 300,
  total_output_tokens: 70,
};

const DAILY = [
  { date: "2026-05-20", cost_usd: 0.5, input_tokens: 80, output_tokens: 20, session_count: 1 },
  { date: "2026-05-22", cost_usd: 0.75, input_tokens: 120, output_tokens: 30, session_count: 2 },
];

describe("<CostPanel />", () => {
  it("renders totals and project rows after fetching the summary", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(jsonResponse(200, SUMMARY)));

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("cost-table")).toBeInTheDocument();
    });
    const totals = screen.getByTestId("cost-totals");
    expect(totals).toHaveTextContent("$1.7500");
    expect(screen.getByText("Demo")).toBeInTheDocument();
    expect(screen.getByText("Experiment")).toBeInTheDocument();
  });

  it("re-fetches the summary when the range preset changes", async () => {
    const seen: string[] = [];
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      seen.push(pathOf(input));
      return Promise.resolve(jsonResponse(200, SUMMARY));
    });

    renderPanel();
    await waitFor(() => {
      expect(seen.length).toBeGreaterThan(0);
    });

    fireEvent.change(screen.getByLabelText(/Range|범위/), {
      target: { value: "all" },
    });

    await waitFor(() => {
      // The "all" preset omits the since param.
      expect(seen.some((p) => p === "/_gapt/api/cost/summary")).toBe(true);
    });
  });

  it("expands a project row and fetches its daily breakdown", async () => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const p = pathOf(input);
      if (p.startsWith("/_gapt/api/projects/p1/cost/daily")) {
        return Promise.resolve(jsonResponse(200, DAILY));
      }
      return Promise.resolve(jsonResponse(200, SUMMARY));
    });

    renderPanel();
    const row = await screen.findByTestId("cost-row-demo");
    fireEvent.click(row);

    await waitFor(() => {
      expect(screen.getByTestId("cost-daily")).toBeInTheDocument();
    });
    expect(screen.getByText("2026-05-20")).toBeInTheDocument();
    expect(screen.getByText("2026-05-22")).toBeInTheDocument();
  });

  it("renders empty-state when no projects have sessions", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        jsonResponse(200, {
          rows: [],
          total_cost_usd: 0,
          total_input_tokens: 0,
          total_output_tokens: 0,
        }),
      ),
    );

    renderPanel();
    await waitFor(() => {
      expect(
        screen.getByText(/No agent sessions in this window|선택 범위에 에이전트 세션이 없습니다/),
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
});
