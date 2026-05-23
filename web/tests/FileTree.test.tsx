import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { FileTree } from "@/ide/FileTree";

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

function pathOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.pathname + input.search;
  return input.url;
}

function mockTreeResponses(map: Record<string, unknown>): void {
  globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
    const path = pathOf(input);
    const match = Object.entries(map).find(([prefix]) => path.includes(prefix));
    if (!match) {
      throw new Error(`Unexpected fetch ${path}`);
    }
    return Promise.resolve(jsonResponse(200, match[1]));
  });
}

function renderTree(onOpen?: (path: string) => void) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <FileTree workspaceId="ws1" onOpenFile={onOpen} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

describe("<FileTree />", () => {
  it("lists root entries on mount", async () => {
    mockTreeResponses({
      "tree?path=%2F": [
        { name: "src", path: "/src", kind: "dir", size: null },
        { name: "README.md", path: "/README.md", kind: "file", size: 42 },
      ],
    });

    renderTree();

    await waitFor(() => {
      expect(screen.getByText("src")).toBeInTheDocument();
    });
    expect(screen.getByText("README.md")).toBeInTheDocument();
  });

  it("expanding a directory loads its children", async () => {
    let lastPath = "";
    globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
      const raw =
        typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const url = new URL(raw.startsWith("http") ? raw : `http://t${raw}`);
      lastPath = url.searchParams.get("path") ?? "";
      if (lastPath === "/") {
        return Promise.resolve(
          jsonResponse(200, [{ name: "src", path: "/src", kind: "dir", size: null }]),
        );
      }
      if (lastPath === "/src") {
        return Promise.resolve(
          jsonResponse(200, [{ name: "main.py", path: "/src/main.py", kind: "file", size: 200 }]),
        );
      }
      throw new Error(`Unexpected path ${lastPath}`);
    });

    renderTree();

    await waitFor(() => {
      expect(screen.getByText("src")).toBeInTheDocument();
    });
    // Click the "src" twisty/row.
    fireEvent.click(screen.getByText("src"));

    await waitFor(() => {
      expect(screen.getByText("main.py")).toBeInTheDocument();
    });
  });

  it("clicking a file invokes onOpenFile with the workspace path", async () => {
    mockTreeResponses({
      "tree?path=%2F": [{ name: "README.md", path: "/README.md", kind: "file", size: 42 }],
    });

    const onOpen = vi.fn();
    renderTree(onOpen);

    const fileBtn = await screen.findByText("README.md");
    fireEvent.click(fileBtn);
    expect(onOpen).toHaveBeenCalledWith("/README.md");
  });

  it("surfaces an inline error when the tree call fails", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(jsonResponse(500, { detail: { code: "server.boom", reason: "no fs" } })),
    );

    renderTree();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("server.boom");
    });
  });
});
