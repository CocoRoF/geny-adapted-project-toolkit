import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/app/providers/I18nProvider";
import { ToolCallCard } from "@/chat/ToolCallCard";
import type { ToolPair } from "@/chat/tool-pair";
import type { SessionStreamEvent } from "@/chat/useSessionStream";

function ev(
  seq: number,
  kind: SessionStreamEvent["kind"],
  data: Record<string, unknown>,
): SessionStreamEvent {
  return { seq, kind, data, ts: new Date(seq * 1000).toISOString() };
}

function renderCard(pair: ToolPair) {
  return render(
    <I18nProvider>
      <ToolCallCard pair={pair} />
    </I18nProvider>,
  );
}

describe("<ToolCallCard />", () => {
  it("renders a running pill while no outcome has arrived", () => {
    renderCard({
      call: ev(1, "tool_call", { tool: "gapt_grep", pattern: "TODO" }),
      result: null,
      error: null,
      running: true,
      abandoned: false,
    });
    expect(screen.getByText(/Running…|실행 중…/)).toBeInTheDocument();
    expect(screen.getByText("gapt_grep")).toBeInTheDocument();
  });

  it("renders an OK pill once the tool_result arrives", () => {
    renderCard({
      call: ev(1, "tool_call", { tool: "gapt_read", path: "/README.md" }),
      result: ev(2, "tool_result", { tool: "gapt_read", content: "hi" }),
      error: null,
      running: false,
      abandoned: false,
    });
    expect(screen.getByText(/^OK$|^성공$/)).toBeInTheDocument();
  });

  it("renders an Error pill plus the friendly exec.* message when the call fails", () => {
    renderCard({
      call: ev(1, "tool_call", { tool: "gapt_edit", path: "/x" }),
      result: null,
      error: ev(2, "error", {
        tool: "gapt_edit",
        exec_code: "exec.tool.access_denied",
        reason: "PolicyEngine denied",
      }),
      running: false,
      abandoned: false,
    });
    expect(screen.getByText(/Failed|실패/)).toBeInTheDocument();
    // Expand to confirm the friendly message renders.
    fireEvent.click(screen.getByRole("button", { name: /Show output|출력 보기/ }));
    expect(
      screen.getByText(/PolicyEngine denied this tool call|PolicyEngine이 이 도구 호출/),
    ).toBeInTheDocument();
  });

  it("expand / collapse toggles the args + result body", () => {
    renderCard({
      call: ev(1, "tool_call", { tool: "gapt_read", path: "/x" }),
      result: ev(2, "tool_result", { tool: "gapt_read", content: "hello" }),
      error: null,
      running: false,
      abandoned: false,
    });
    expect(screen.queryByText("hello")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Show output|출력 보기/ }));
    expect(screen.getByText(/"content"/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Hide output|출력 숨기기/ }));
    expect(screen.queryByText(/"content"/)).not.toBeInTheDocument();
  });
});
