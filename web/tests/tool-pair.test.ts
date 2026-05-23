import { describe, expect, it } from "vitest";

import { pairToolEvents } from "@/chat/tool-pair";
import type { SessionStreamEvent } from "@/chat/useSessionStream";

function ev(
  seq: number,
  kind: SessionStreamEvent["kind"],
  data: Record<string, unknown>,
): SessionStreamEvent {
  return { seq, kind, data, ts: new Date(seq * 1000).toISOString() };
}

describe("pairToolEvents", () => {
  it("pairs tool_call with the next matching tool_result", () => {
    const pairs = pairToolEvents([
      ev(1, "tool_call", { tool: "gapt_read", path: "/x" }),
      ev(2, "tool_result", { tool: "gapt_read", content: "hi" }),
    ]);
    expect(pairs).toHaveLength(1);
    const first = pairs[0]!;
    expect(first.running).toBe(false);
    expect(first.result?.seq).toBe(2);
    expect(first.error).toBeNull();
  });

  it("leaves a tool_call running when no outcome has arrived yet", () => {
    const pairs = pairToolEvents([ev(1, "tool_call", { tool: "gapt_grep", pattern: "x" })]);
    const first = pairs[0]!;
    expect(first.running).toBe(true);
    expect(first.result).toBeNull();
  });

  it("attaches an `error` frame to the right call", () => {
    const pairs = pairToolEvents([
      ev(1, "tool_call", { tool: "gapt_edit", path: "/x" }),
      ev(2, "error", { tool: "gapt_edit", exec_code: "exec.tool.invalid_input" }),
    ]);
    const first = pairs[0]!;
    expect(first.running).toBe(false);
    expect(first.error?.data["exec_code"]).toBe("exec.tool.invalid_input");
  });

  it("uses call_id when present to pair across interleaved tools", () => {
    const pairs = pairToolEvents([
      ev(1, "tool_call", { tool: "gapt_read", call_id: "a" }),
      ev(2, "tool_call", { tool: "gapt_read", call_id: "b" }),
      ev(3, "tool_result", { tool: "gapt_read", call_id: "b", result: "B" }),
      ev(4, "tool_result", { tool: "gapt_read", call_id: "a", result: "A" }),
    ]);
    expect(pairs).toHaveLength(2);
    expect(pairs[0]!.result?.data["result"]).toBe("A");
    expect(pairs[1]!.result?.data["result"]).toBe("B");
  });
});
