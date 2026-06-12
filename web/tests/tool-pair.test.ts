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

  describe("abandoned (Phase N.3 — terminal-event cleanup)", () => {
    it("marks an open tool_call abandoned when the turn ends with `done`", () => {
      // Reproduces the live bug: agent emitted PRE_TOOL_USE, then died
      // (budget / crash) before POST_TOOL_USE. The `done` event still
      // fired (lifecycle handler is `try/except/finally`-shaped), but
      // pre-fix the UI showed the tool as "실행 중..." forever.
      const pairs = pairToolEvents([
        ev(1, "tool_call", { tool: "gapt_read", call_id: "a" }),
        ev(2, "done", { cost: { cost_usd: 0.01 } }),
      ]);
      const first = pairs[0]!;
      expect(first.running).toBe(false);
      expect(first.abandoned).toBe(true);
      expect(first.result).toBeNull();
      expect(first.error).toBeNull();
    });

    it("marks an open tool_call abandoned on a session-level error", () => {
      // Session-level error events have no tool_use_id / call_id and
      // therefore don't match any pending pair — but they DO signal
      // that the agent is dead, so any open call must stop spinning.
      const pairs = pairToolEvents([
        ev(1, "tool_call", { tool: "gapt_read", call_id: "a" }),
        ev(2, "error", { exec_code: "exec.session.crashed" }),
      ]);
      const first = pairs[0]!;
      expect(first.running).toBe(false);
      expect(first.abandoned).toBe(true);
    });

    it("does NOT mark a call abandoned when only a tool-level error matches it", () => {
      // Tool-level error has a matching key → attaches to the pair as
      // `error`, NOT as `abandoned`. Distinct UX: error has a real
      // payload; abandoned means "we don't know what happened".
      const pairs = pairToolEvents([
        ev(1, "tool_call", { tool: "gapt_edit", call_id: "a" }),
        ev(2, "error", {
          tool: "gapt_edit",
          call_id: "a",
          exec_code: "exec.tool.invalid_input",
        }),
      ]);
      const first = pairs[0]!;
      expect(first.running).toBe(false);
      expect(first.abandoned).toBe(false);
      expect(first.error?.data["exec_code"]).toBe("exec.tool.invalid_input");
    });

    it("leaves completed pairs alone when a later turn opens + abandons", () => {
      const pairs = pairToolEvents([
        ev(1, "tool_call", { tool: "gapt_read", call_id: "a" }),
        ev(2, "tool_result", { tool: "gapt_read", call_id: "a", result: "ok" }),
        ev(3, "done", {}),
        ev(4, "tool_call", { tool: "gapt_edit", call_id: "b" }),
        ev(5, "done", {}),
      ]);
      expect(pairs).toHaveLength(2);
      expect(pairs[0]!.abandoned).toBe(false);
      expect(pairs[0]!.result?.data["result"]).toBe("ok");
      expect(pairs[1]!.abandoned).toBe(true);
    });

    it("keeps a live pair running while the turn hasn't terminated", () => {
      // Boundary: no `done` and no terminal error yet → still
      // legitimately running.
      const pairs = pairToolEvents([ev(1, "tool_call", { tool: "gapt_read", call_id: "a" })]);
      const first = pairs[0]!;
      expect(first.running).toBe(true);
      expect(first.abandoned).toBe(false);
    });
  });
});
