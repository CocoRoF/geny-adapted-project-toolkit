import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { CiPanel } from "@/ci/CiPanel";

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
});

function renderPanel() {
  return render(
    <I18nProvider>
      <CiPanel projectId="p1" />
    </I18nProvider>,
  );
}

const SAMPLE = [
  {
    id: 101,
    name: "CI / build",
    head_branch: "main",
    head_sha: "abc1234567",
    status: "completed_success",
    html_url: "https://github.com/owner/repo/actions/runs/101",
  },
  {
    id: 102,
    name: "CI / test",
    head_branch: "feature/x",
    head_sha: "def4567890",
    status: "completed_failure",
    html_url: "https://github.com/owner/repo/actions/runs/102",
  },
];

describe("<CiPanel />", () => {
  it("renders the workflow runs after fetching", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(jsonResponse(200, SAMPLE)));

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("ci-table")).toBeInTheDocument();
    });
    expect(screen.getByText("CI / build")).toBeInTheDocument();
    expect(screen.getByText("CI / test")).toBeInTheDocument();
    // SHAs are abbreviated to 7 chars.
    expect(screen.getByText("abc1234")).toBeInTheDocument();
  });

  it("renders the empty-state when no runs come back", async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve(jsonResponse(200, [])));

    renderPanel();

    await waitFor(() => {
      expect(
        screen.getByText(/No workflow runs|워크플로 실행 기록이 없습니다/),
      ).toBeInTheDocument();
    });
  });

  it("surfaces ci.no_token as a help-shaped error banner", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        jsonResponse(412, {
          detail: { code: "ci.no_token", reason: "no GitHub token configured" },
        }),
      ),
    );

    renderPanel();

    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent("ci.no_token");
      expect(alert).toHaveAttribute("data-error-code", "ci.no_token");
    });
  });
});
