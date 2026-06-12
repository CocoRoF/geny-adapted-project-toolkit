import type { SideView } from "@/ide/shell/ActivityBar";
import type { BottomTab } from "@/ide/shell/BottomPanel";

/** Persisted IDE shell layout — one localStorage entry per
 * workspace. Lives in its own module (not IdeShell.tsx) so tests
 * and non-component consumers can import the presets without
 * tripping react-refresh's only-export-components rule. */
export interface LayoutState {
  sideView: SideView | null;
  sideWidth: number;
  bottomTab: BottomTab | null;
  bottomHeight: number;
  chatOpen: boolean;
  chatWidth: number;
  /** Phase F — Editor column visible? When false, the editor area
   *  hides entirely and the Chat panel grows to fill the freed
   *  space (or SidePanel + Chat split if Chat is also closed).
   *  Auto-opens when a file or diff is selected. */
  editorOpen: boolean;
}

export const DEFAULT_LAYOUT: LayoutState = {
  sideView: "files",
  sideWidth: 260,
  bottomTab: null,
  bottomHeight: 240,
  chatOpen: true,
  chatWidth: 480,
  editorOpen: true,
};

/** Phase D.5 — Named layout presets selectable from the palette
 *  (Cmd/Ctrl+K). Each preset is a complete `LayoutState` so
 *  switching is a single set-state call. Operator-saved layouts
 *  (current behaviour: workspace localStorage entry) survive a
 *  preset switch — selecting "default" doesn't wipe the LS value
 *  for OTHER workspaces, only the current one's. */
export const LAYOUT_PRESETS = {
  default: DEFAULT_LAYOUT,
  chat_focused: {
    sideView: null,
    sideWidth: 260,
    bottomTab: null,
    bottomHeight: 240,
    chatOpen: true,
    chatWidth: 720,
    editorOpen: false,
  },
  debug: {
    sideView: "files",
    sideWidth: 240,
    bottomTab: "terminal",
    bottomHeight: 280,
    chatOpen: true,
    chatWidth: 420,
    editorOpen: true,
  },
  minimal: {
    sideView: null,
    sideWidth: 260,
    bottomTab: null,
    bottomHeight: 240,
    chatOpen: false,
    chatWidth: 380,
    editorOpen: true,
  },
} satisfies Record<string, LayoutState>;
