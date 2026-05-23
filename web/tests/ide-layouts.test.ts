import { describe, expect, it } from "vitest";

import { ALL_PRESETS, PRESETS } from "@/ide/layouts";

describe("dockview layout presets", () => {
  it("exposes 4 named presets", () => {
    expect(ALL_PRESETS).toEqual(["focus", "review", "debug", "custom"]);
  });

  it("each preset is a SerializedDockview with at least one panel", () => {
    for (const name of ALL_PRESETS) {
      const layout = PRESETS[name];
      expect(layout.grid).toBeDefined();
      expect(Object.keys(layout.panels).length).toBeGreaterThan(0);
    }
  });

  it("focus preset has the canonical [tree | editor | chat] layout", () => {
    const layout = PRESETS.focus;
    expect(Object.keys(layout.panels).sort()).toEqual(["chat", "editor", "tree"]);
  });

  it("review preset surfaces the diff panel + audit panel", () => {
    const layout = PRESETS.review;
    expect(Object.keys(layout.panels)).toContain("diff");
    // Cycle 3.13 swapped the "CI" slot for the audit panel because
    // CI streaming is deferred to M1-E4. The slot stays available.
    expect(Object.keys(layout.panels)).toContain("audit");
  });

  it("debug preset surfaces the terminal panel", () => {
    const layout = PRESETS.debug;
    expect(Object.keys(layout.panels)).toContain("terminal");
  });

  it("custom preset defaults to a focus baseline", () => {
    expect(PRESETS.custom).toBe(PRESETS.focus);
  });
});
