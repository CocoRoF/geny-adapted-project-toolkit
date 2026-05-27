import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { ThemeProvider } from "@/app/providers/ThemeProvider";

// Mock @monaco-editor/react before importing the editor component.
// happy-dom can't render the real Monaco editor and we don't need
// to exercise it — we just want to assert the surrounding state
// machine (load / dirty / saving / saved).
vi.mock("@monaco-editor/react", () => ({
  __esModule: true,
  default: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (value: string | undefined) => void;
  }) => (
    <textarea
      data-testid="monaco-stub"
      value={value}
      onChange={(e) => onChange(e.currentTarget.value)}
    />
  ),
}));

import { FileEditor } from "@/ide/Editor";

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

function renderEditor(openPath: string | null) {
  return render(
    <ThemeProvider>
      <I18nProvider>
        <FileEditor workspaceId="ws1" openPath={openPath} />
      </I18nProvider>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.clearAllMocks();
});

describe("<FileEditor />", () => {
  it("renders empty-state when no file is open", () => {
    renderEditor(null);
    expect(screen.getByText(/No file open|열린 파일이 없습니다/)).toBeInTheDocument();
  });

  it("loads the file and shows a Monaco surface for utf-8 content", async () => {
    mockRoutes([
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/workspaces/ws1/file"),
        handler: () =>
          jsonResponse(200, { path: "/src/foo.py", encoding: "utf-8", text: "print('hi')\n" }),
      },
    ]);

    renderEditor("/src/foo.py");

    const textarea = await waitFor(() => screen.getByTestId<HTMLTextAreaElement>("monaco-stub"), {
      timeout: 2000,
    });
    expect(textarea.value).toBe("print('hi')\n");
    expect(screen.getByText("/src/foo.py")).toBeInTheDocument();
  });

  it("flags binary files as non-editable", async () => {
    mockRoutes([
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/workspaces/ws1/file"),
        handler: () =>
          jsonResponse(200, { path: "/img.png", encoding: "base64", text: "iVBORw..." }),
      },
    ]);

    renderEditor("/img.png");

    await waitFor(() => {
      expect(screen.getByTestId("editor-binary")).toBeInTheDocument();
    });
  });

  it("surfaces an error message when the load fails", async () => {
    mockRoutes([
      {
        match: (input) => pathOf(input).startsWith("/_gapt/api/workspaces/ws1/file"),
        handler: () =>
          jsonResponse(404, { detail: { code: "workspace.fs.not_found", reason: "missing" } }),
      },
    ]);

    renderEditor("/missing");

    await waitFor(() => {
      expect(screen.getByText(/workspace\.fs\.not_found/)).toBeInTheDocument();
    });
  });
});
