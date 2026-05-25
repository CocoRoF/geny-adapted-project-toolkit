import { describe, expect, it } from "vitest";

import {
  CHAT_ID,
  EDITOR_GROUP_ID,
  EDITOR_ID,
  IDE_BASELINE,
  TREE_ID,
} from "@/ide/layouts";

describe("IDE baseline layout", () => {
  it("contains exactly the three baseline panels (tree, editor, chat)", () => {
    expect(Object.keys(IDE_BASELINE.panels).sort()).toEqual(
      [CHAT_ID, EDITOR_ID, TREE_ID].sort(),
    );
  });

  it("starts focused on the editor group", () => {
    expect(IDE_BASELINE.activeGroup).toBe(EDITOR_GROUP_ID);
  });

  it("each baseline panel references a real component kind", () => {
    for (const panel of Object.values(IDE_BASELINE.panels)) {
      expect(typeof panel.contentComponent).toBe("string");
    }
  });
});
