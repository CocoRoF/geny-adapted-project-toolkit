import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { DiffCard } from "@/chat/DiffCard";
import { INLINE_THRESHOLD_LINES, countLines, unifiedDiff } from "@/chat/diff-util";

// Mock Monaco's DiffEditor — same reason as Editor.test.tsx.
vi.mock("@monaco-editor/react", () => ({
  __esModule: true,
  DiffEditor: ({ original, modified }: { original: string; modified: string }) => (
    <div data-testid="monaco-diff">
      <pre data-testid="diff-original">{original}</pre>
      <pre data-testid="diff-modified">{modified}</pre>
    </div>
  ),
}));

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.clearAllMocks();
});

function renderCard(props: Parameters<typeof DiffCard>[0]) {
  return render(
    <I18nProvider>
      <DiffCard {...props} />
    </I18nProvider>,
  );
}

describe("diff-util", () => {
  it("countLines reports 0 for empty string, n for n-line input", () => {
    expect(countLines("")).toBe(0);
    expect(countLines("a")).toBe(1);
    expect(countLines("a\nb\nc")).toBe(3);
  });

  it("unifiedDiff splits the two sides on newlines", () => {
    const diff = unifiedDiff("a\nb", "a\nc");
    expect(diff.removed).toEqual(["a", "b"]);
    expect(diff.added).toEqual(["a", "c"]);
  });
});

describe("<DiffCard />", () => {
  it("renders the path, replaced count, and inline diff for a small edit", () => {
    renderCard({
      workspaceId: "w1",
      payload: { path: "/src/x.py", old: "a = 1", new: "a = 2", replaced: 1 },
    });

    expect(screen.getByText("/src/x.py")).toBeInTheDocument();
    expect(screen.getByTestId("diff-inline")).toBeInTheDocument();
    expect(screen.getByText(/1.*(replacement|치환)/)).toBeInTheDocument();
  });

  it("switches to Monaco DiffEditor for edits over the inline threshold", () => {
    const old = Array.from({ length: INLINE_THRESHOLD_LINES + 5 }, (_, i) => `old-${i}`).join("\n");
    const next = Array.from({ length: INLINE_THRESHOLD_LINES + 5 }, (_, i) => `new-${i}`).join(
      "\n",
    );

    renderCard({
      workspaceId: "w1",
      payload: { path: "/big.py", old, new: next },
    });

    // Default mode is still inline; the toggle button opens Monaco.
    expect(screen.queryByTestId("monaco-diff")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /side-by-side|사이드 바이 사이드/ }));
    expect(screen.getByTestId("monaco-diff")).toBeInTheDocument();
  });

  it("reverts an edit by reading the current file and writing the inverse", async () => {
    const recorded: Array<{ method: string; path: string; body: unknown }> = [];
    globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : null;
      recorded.push({ method, path: raw, body });
      if (method === "GET") {
        return Promise.resolve(
          jsonResponse(200, { path: "/x.py", encoding: "utf-8", text: "a = 2\n" }),
        );
      }
      return Promise.resolve(
        jsonResponse(200, { path: "/x.py", encoding: "utf-8", text: "a = 1\n" }),
      );
    });

    renderCard({
      workspaceId: "w1",
      payload: { path: "/x.py", old: "a = 1", new: "a = 2", replaced: 1 },
    });

    fireEvent.click(screen.getByRole("button", { name: /Revert|되돌리기/ }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Reverted|되돌렸습니다/ })).toBeInTheDocument();
    });
    expect(recorded.find((r) => r.method === "PUT")?.body).toEqual({
      content: "a = 1\n",
      encoding: "utf-8",
    });
  });
});
