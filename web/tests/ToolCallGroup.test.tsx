import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { ToolCallGroup } from "@/chat/ToolCallGroup";
import type { ToolPair } from "@/chat/tool-pair";
import type { SessionStreamEvent } from "@/chat/useSessionStream";

function ev(
  seq: number,
  kind: SessionStreamEvent["kind"],
  data: Record<string, unknown>,
): SessionStreamEvent {
  return { seq, kind, data, ts: new Date(seq * 1000).toISOString() };
}

function pair(name: string, opts: { running?: boolean; error?: boolean } = {}): ToolPair {
  const seq = Math.floor(Math.random() * 1000);
  const call = ev(seq, "tool_call", { tool: name, tool_use_id: `t-${seq}` });
  if (opts.error) {
    return {
      call,
      result: null,
      error: ev(seq + 1, "error", { exec_code: "exec.tool.failed", tool_use_id: `t-${seq}` }),
      running: false,
      abandoned: false,
    };
  }
  if (opts.running) {
    return { call, result: null, error: null, running: true, abandoned: false };
  }
  return {
    call,
    result: ev(seq + 1, "tool_result", { tool: name, tool_use_id: `t-${seq}`, content: "ok" }),
    error: null,
    running: false,
    abandoned: false,
  };
}

function renderGroup(pairs: ToolPair[], defaultOpen = false) {
  return render(
    <I18nProvider>
      <ToolCallGroup pairs={pairs} defaultOpen={defaultOpen} />
    </I18nProvider>,
  );
}

describe("<ToolCallGroup />", () => {
  it("falls back to a flat single card when only one pair is supplied", () => {
    renderGroup([pair("Bash")]);
    // No group wrapper — flat ToolCallCard. Single tool-card, no
    // tool-group container.
    expect(screen.queryByTestId("tool-group")).toBeNull();
    expect(screen.getByTestId("tool-card")).toBeInTheDocument();
  });

  it("renders a single collapsible header for multiple pairs", () => {
    renderGroup([pair("Bash"), pair("Bash"), pair("Read")]);
    const group = screen.getByTestId("tool-group");
    expect(group).toBeInTheDocument();
    expect(group.getAttribute("data-tool-count")).toBe("3");
    // Header summary surfaces the multiplicities.
    expect(screen.getByText(/Bash ×2/)).toBeInTheDocument();
    expect(screen.getByText(/Read/)).toBeInTheDocument();
    // Children cards are NOT in the DOM while collapsed (default).
    expect(screen.queryByTestId("tool-card")).toBeNull();
  });

  it("expands and reveals the individual tool cards on click", () => {
    renderGroup([pair("Bash"), pair("Read"), pair("Grep")]);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    // Now the inner ToolCallCards mount; one per pair.
    expect(screen.getAllByTestId("tool-card")).toHaveLength(3);
  });

  it("propagates error status to the group header when any pair failed", () => {
    renderGroup([pair("Bash"), pair("Read", { error: true })]);
    expect(screen.getByText(/Failed|실패/)).toBeInTheDocument();
  });

  it("shows running on the header while any pair is in flight", () => {
    renderGroup([pair("Bash"), pair("Read", { running: true })]);
    expect(screen.getByText(/Running…|실행 중…/)).toBeInTheDocument();
  });

  it("shows success only when every pair settled with a result", () => {
    renderGroup([pair("Bash"), pair("Read"), pair("Grep")]);
    expect(screen.getByText(/^OK$|^성공$/)).toBeInTheDocument();
  });

  it("respects defaultOpen", () => {
    renderGroup([pair("Bash"), pair("Read")], true);
    expect(screen.getAllByTestId("tool-card")).toHaveLength(2);
  });
});
