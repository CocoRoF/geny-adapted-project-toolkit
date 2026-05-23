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

  it("review preset surfaces the diff panel + CI panel", () => {
    const layout = PRESETS.review;
    expect(Object.keys(layout.panels)).toContain("diff");
    expect(Object.keys(layout.panels)).toContain("ci");
  });

  it("debug preset surfaces the terminal panel", () => {
    const layout = PRESETS.debug;
    expect(Object.keys(layout.panels)).toContain("terminal");
  });

  it("custom preset defaults to a focus baseline", () => {
    expect(PRESETS.custom).toBe(PRESETS.focus);
  });
});
