import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthProvider } from "@/app/providers/AuthProvider";
import { I18nProvider } from "@/app/providers/I18nProvider";
import { ProjectDetail } from "@/routes/ProjectDetail";

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

function mockFetchRoutes(routes: Route[]): void {
  globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const matched = routes.find((r) => r.match(input, init));
    if (!matched) {
      const desc =
        typeof input === "string" ? input : input instanceof URL ? input.href : "request";
      throw new Error(`Unexpected fetch ${init?.method ?? "GET"} ${desc}`);
    }
    return Promise.resolve(matched.handler());
  });
}

function pathOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.pathname;
  return input.url;
}

const ALICE_ME = {
  user_id: "u1",
  email: "alice@example.com",
  display_name: null,
  orgs: [{ org_id: "o1", org_slug: "default", role: "owner" }],
};

const PROJECT = {
  id: "p1",
  org_id: "o1",
  owner_id: "u1",
  slug: "demo",
  display_name: "Demo",
  git_remote_url: "https://example.com/demo.git",
  git_provider: "github",
  git_auth_secret_ref: null,
  default_compose_paths: [],
  compose_profile_dev: null,
  compose_profile_prod: null,
  created_at: "2026-05-23T00:00:00Z",
  archived_at: null,
};

const WORKSPACE = {
  id: "w1",
  project_id: "p1",
  branch: "main",
  worktree_path: "/workspace",
  sandbox_id: "sb1",
  status: "running",
  last_activity_at: "2026-05-23T00:00:00Z",
  created_at: "2026-05-23T00:00:00Z",
};

function renderProjectDetail() {
  return render(
    <MemoryRouter initialEntries={["/projects/p1"]}>
      <I18nProvider>
        <AuthProvider>
          <Routes>
            <Route path="/projects/:pid" element={<ProjectDetail />} />
          </Routes>
        </AuthProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.clearAllMocks();
});

describe("<ProjectDetail />", () => {
  it("renders project header and workspace list", async () => {
    mockFetchRoutes([
      {
        match: (input) => pathOf(input).startsWith("/api/auth/me"),
        handler: () => jsonResponse(200, ALICE_ME),
      },
      {
        match: (input) => pathOf(input) === "/api/projects/p1",
        handler: () => jsonResponse(200, PROJECT),
      },
      {
        match: (input) => pathOf(input) === "/api/projects/p1/workspaces",
        handler: () => jsonResponse(200, [WORKSPACE]),
      },
    ]);

    renderProjectDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Demo" })).toBeInTheDocument();
    });
    expect(screen.getByText(/main/)).toBeInTheDocument();
    const openLink = screen.getByRole("link", { name: /Open|열기/ });
    expect(openLink).toHaveAttribute("href", "/projects/p1/w/w1");
  });

  it("stops a running workspace via the action button", async () => {
    mockFetchRoutes([
      {
        match: (input) => pathOf(input).startsWith("/api/auth/me"),
        handler: () => jsonResponse(200, ALICE_ME),
      },
      {
        match: (input) => pathOf(input) === "/api/projects/p1",
        handler: () => jsonResponse(200, PROJECT),
      },
      {
        match: (input) => pathOf(input) === "/api/projects/p1/workspaces",
        handler: () => jsonResponse(200, [WORKSPACE]),
      },
      {
        match: (input, init) =>
          pathOf(input) === "/api/workspaces/w1/stop" && init?.method === "POST",
        handler: () => jsonResponse(200, { ...WORKSPACE, status: "stopped" }),
      },
    ]);

    renderProjectDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Demo" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Stop|정지/ }));

    await waitFor(() => {
      expect(screen.getByText(/Stopped|정지됨/)).toBeInTheDocument();
    });
  });

  it("shows an empty-state when no workspaces exist", async () => {
    mockFetchRoutes([
      {
        match: (input) => pathOf(input).startsWith("/api/auth/me"),
        handler: () => jsonResponse(200, ALICE_ME),
      },
      {
        match: (input) => pathOf(input) === "/api/projects/p1",
        handler: () => jsonResponse(200, PROJECT),
      },
      {
        match: (input) => pathOf(input) === "/api/projects/p1/workspaces",
        handler: () => jsonResponse(200, []),
      },
    ]);

    renderProjectDetail();

    await waitFor(() => {
      expect(screen.getByText(/No workspaces yet|워크스페이스가 없습니다/)).toBeInTheDocument();
    });
  });
});
