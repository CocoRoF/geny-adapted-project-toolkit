import { describe, expect, it } from "vitest";

import { DEFAULT_LAYOUT, LAYOUT_PRESETS } from "@/ide/shell/layouts";

/** The palette-selectable layout presets (Phase D.5) — successor of
 * the old dockview-era `@/ide/layouts` baseline this file used to
 * cover. Guards the invariants the IdeShell relies on when applying
 * a preset wholesale via a single set-state. */
describe("IDE layout presets", () => {
  it("ships the four named presets", () => {
    expect(Object.keys(LAYOUT_PRESETS).sort()).toEqual(
      ["chat_focused", "debug", "default", "minimal"].sort(),
    );
  });

  it("every preset is a COMPLETE LayoutState (same keys as default)", () => {
    const wanted = Object.keys(DEFAULT_LAYOUT).sort();
    for (const [name, preset] of Object.entries(LAYOUT_PRESETS)) {
      expect(Object.keys(preset).sort(), `preset ${name}`).toEqual(wanted);
    }
  });

  it("default preset IS the default layout", () => {
    expect(LAYOUT_PRESETS.default).toEqual(DEFAULT_LAYOUT);
  });

  it("chat_focused collapses the editor and keeps chat open", () => {
    expect(LAYOUT_PRESETS.chat_focused?.editorOpen).toBe(false);
    expect(LAYOUT_PRESETS.chat_focused?.chatOpen).toBe(true);
  });

  it("debug opens the terminal; minimal closes the chat", () => {
    expect(LAYOUT_PRESETS.debug?.bottomTab).toBe("terminal");
    expect(LAYOUT_PRESETS.minimal?.chatOpen).toBe(false);
  });
});
