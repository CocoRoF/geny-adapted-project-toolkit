import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthProvider } from "@/app/providers/AuthProvider";
import { I18nProvider } from "@/app/providers/I18nProvider";
import { ProjectsIndex } from "@/routes/ProjectsIndex";

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
      const method = init?.method ?? "GET";
      throw new Error(`Unexpected fetch ${method} ${desc}`);
    }
    return Promise.resolve(matched.handler());
  });
}

function pathOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.pathname + input.search;
  return input.url;
}

const ALICE_ME = {
  user_id: "u1",
  email: "alice@example.com",
  display_name: null,
  orgs: [{ org_id: "o1", org_slug: "default", role: "owner" }],
};

const PROJECT_A = {
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

function renderProjectsIndex() {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <AuthProvider>
          <ProjectsIndex />
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

describe("<ProjectsIndex />", () => {
  it("renders the empty-state message when no projects exist", async () => {
    mockFetchRoutes([
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/auth/me"),
        handler: () => jsonResponse(200, ALICE_ME),
      },
      {
        match: (input) =>
          pathOf(input).startsWith("/_gapt/api/projects") && pathOf(input).indexOf("?") < 0,
        handler: () => jsonResponse(200, []),
      },
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/projects?"),
        handler: () => jsonResponse(200, []),
      },
    ]);

    renderProjectsIndex();

    await waitFor(() => {
      expect(screen.getByText(/No projects yet|프로젝트가 없습니다/)).toBeInTheDocument();
    });
  });

  it("renders project cards when the list is non-empty", async () => {
    mockFetchRoutes([
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/auth/me"),
        handler: () => jsonResponse(200, ALICE_ME),
      },
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/projects"),
        handler: () => jsonResponse(200, [PROJECT_A]),
      },
    ]);

    renderProjectsIndex();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Demo" })).toBeInTheDocument();
    });
    const card = screen.getByRole("link", { name: /Demo/ });
    expect(card).toHaveAttribute("href", "/projects/p1");
  });

  it("surfaces an inline alert when the list call fails", async () => {
    mockFetchRoutes([
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/auth/me"),
        handler: () => jsonResponse(200, ALICE_ME),
      },
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/projects"),
        handler: () => jsonResponse(500, { detail: { code: "server.boom", reason: "database" } }),
      },
    ]);

    renderProjectsIndex();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("server.boom");
    });
  });

  it("opens the create modal and submits a new project", async () => {
    mockFetchRoutes([
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/auth/me"),
        handler: () => jsonResponse(200, ALICE_ME),
      },
      {
        match: (input, init) =>
          pathOf(input).startsWith("/_gapt/api/projects") && (init?.method ?? "GET") === "GET",
        handler: () => jsonResponse(200, []),
      },
      {
        match: (input, init) => pathOf(input) === "/_gapt/api/projects" && init?.method === "POST",
        handler: () => jsonResponse(201, PROJECT_A),
      },
    ]);

    renderProjectsIndex();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /New project|새 프로젝트/ })).toBeEnabled();
    });
    // Phase N.2.6 — "+ 새 프로젝트" is a dropdown now; the git-URL
    // import modal sits behind the "불러오기" menu item.
    fireEvent.click(screen.getByRole("button", { name: /New project|새 프로젝트/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /불러오기|Import/ }));

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(screen.getByLabelText(/Display name|표시 이름/), {
      target: { value: "Demo" },
    });
    fireEvent.change(screen.getByLabelText(/Slug|슬러그/), { target: { value: "demo" } });
    fireEvent.change(screen.getByLabelText(/Git remote URL|Git 원격 URL/), {
      target: { value: "https://example.com/demo.git" },
    });
    fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Demo" })).toBeInTheDocument();
    });
  });
});
