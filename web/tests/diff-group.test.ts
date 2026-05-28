import { describe, expect, it } from "vitest";

import type { GaptEditPayload } from "@/chat/DiffCard";
import { annotateEditGroups } from "@/chat/diff-group";
import type { SessionStreamEvent } from "@/chat/useSessionStream";

function editEvent(seq: number, path: string): SessionStreamEvent {
  return {
    seq,
    kind: "tool_result",
    ts: "2026-05-28T00:00:00Z",
    data: {
      tool: "gapt_edit",
      metadata: { path, old: "a", new: "b" },
    },
  };
}

function nonEdit(seq: number, kind: SessionStreamEvent["kind"]): SessionStreamEvent {
  return {
    seq,
    kind,
    ts: "2026-05-28T00:00:00Z",
    data: {},
  };
}

function extract(data: Record<string, unknown>): GaptEditPayload | null {
  const meta = data["metadata"];
  if (!meta || typeof meta !== "object") return null;
  const m = meta as Record<string, unknown>;
  if (
    typeof m["path"] !== "string" ||
    typeof m["old"] !== "string" ||
    typeof m["new"] !== "string"
  ) {
    return null;
  }
  return { path: m["path"], old: m["old"], new: m["new"] };
}

describe("annotateEditGroups", () => {
  it("groups consecutive edits to the same file", () => {
    const events = [
      editEvent(1, "src/foo.py"),
      editEvent(2, "src/foo.py"),
      editEvent(3, "src/foo.py"),
    ];
    const markers = annotateEditGroups(events, extract);
    expect(markers.get(1)).toEqual({ groupSize: 3, groupIndex: 0, path: "src/foo.py" });
    expect(markers.get(2)).toEqual({ groupSize: 3, groupIndex: 1, path: "src/foo.py" });
    expect(markers.get(3)).toEqual({ groupSize: 3, groupIndex: 2, path: "src/foo.py" });
  });

  it("starts a new group when the path changes", () => {
    const events = [
      editEvent(1, "src/a.py"),
      editEvent(2, "src/a.py"),
      editEvent(3, "src/b.py"),
    ];
    const markers = annotateEditGroups(events, extract);
    expect(markers.get(1)?.groupSize).toBe(2);
    expect(markers.get(2)?.groupIndex).toBe(1);
    expect(markers.get(3)?.groupSize).toBe(1);
    expect(markers.get(3)?.path).toBe("src/b.py");
  });

  it("breaks a run when a non-edit event slips in between", () => {
    // Common: agent emits a `text` thought between two edits to the
    // same file. We treat them as separate groups so the header
    // doesn't span the unrelated frame.
    const events = [
      editEvent(1, "src/a.py"),
      nonEdit(2, "text"),
      editEvent(3, "src/a.py"),
    ];
    const markers = annotateEditGroups(events, extract);
    expect(markers.get(1)?.groupSize).toBe(1);
    expect(markers.get(3)?.groupSize).toBe(1);
  });

  it("ignores tool_result events that aren't gapt_edit", () => {
    const events = [
      editEvent(1, "src/a.py"),
      {
        seq: 2,
        kind: "tool_result" as const,
        ts: "2026-05-28T00:00:00Z",
        data: { tool: "gapt_read", metadata: { path: "src/a.py" } },
      },
      editEvent(3, "src/a.py"),
    ];
    const markers = annotateEditGroups(events, extract);
    // Read between two edits breaks the run — group of 1 each.
    expect(markers.get(1)?.groupSize).toBe(1);
    expect(markers.get(3)?.groupSize).toBe(1);
  });

  it("returns no markers when there are no edits", () => {
    const events = [nonEdit(1, "text"), nonEdit(2, "tool_call")];
    const markers = annotateEditGroups(events, extract);
    expect(markers.size).toBe(0);
  });
});
