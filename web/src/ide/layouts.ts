import { Orientation, type SerializedDockview } from "dockview";

/** Layout preset library.
 *
 * Each preset is a `SerializedDockview` snapshot dockview can load
 * via `api.fromJSON(layout)`. Three built-in presets (Focus / Review
 * / Debug) per plan §3.3 + a `custom` slot the user populates by
 * dragging — `custom` defaults to Focus until the user changes it.
 *
 * Panel ids match what `PanelPlaceholder` expects: `tree`, `editor`,
 * `diff`, `terminal`, `preview`, `chat`, `ci`. */

export type LayoutPreset = "focus" | "review" | "debug" | "custom";

function placeholder(id: string, title: string, kind: string) {
  return {
    id,
    contentComponent: "placeholder",
    title,
    params: { kind },
  };
}

function treePanel(id: string = "tree", title: string = "Files") {
  return {
    id,
    contentComponent: "tree",
    title,
    // `workspaceId` is injected at runtime by `DockviewShell`'s
    // `onReady` — `params` here is a placeholder we never persist.
    params: { workspaceId: "", kind: "tree" },
  };
}

const FOCUS: SerializedDockview = {
  grid: {
    height: 1000,
    width: 1600,
    orientation: Orientation.HORIZONTAL,
    root: {
      type: "branch",
      data: [
        {
          type: "leaf",
          size: 220,
          data: { views: ["tree"], activeView: "tree", id: "tree-group" },
        },
        {
          type: "leaf",
          size: 880,
          data: { views: ["editor"], activeView: "editor", id: "editor-group" },
        },
        {
          type: "leaf",
          size: 500,
          data: { views: ["chat"], activeView: "chat", id: "chat-group" },
        },
      ],
    },
  },
  panels: {
    tree: treePanel(),
    editor: placeholder("editor", "Editor", "editor"),
    chat: placeholder("chat", "Chat", "chat"),
  },
  activeGroup: "editor-group",
};

const REVIEW: SerializedDockview = {
  grid: {
    height: 1000,
    width: 1600,
    orientation: Orientation.HORIZONTAL,
    root: {
      type: "branch",
      data: [
        {
          type: "leaf",
          size: 220,
          data: { views: ["tree"], activeView: "tree", id: "tree-group" },
        },
        {
          type: "leaf",
          size: 880,
          data: { views: ["diff"], activeView: "diff", id: "diff-group" },
        },
        {
          type: "branch",
          size: 500,
          data: [
            {
              type: "leaf",
              size: 600,
              data: { views: ["chat"], activeView: "chat", id: "chat-group" },
            },
            {
              type: "leaf",
              size: 400,
              data: { views: ["ci"], activeView: "ci", id: "ci-group" },
            },
          ],
        },
      ],
    },
  },
  panels: {
    tree: treePanel(),
    diff: placeholder("diff", "Diff", "diff"),
    chat: placeholder("chat", "Chat", "chat"),
    ci: placeholder("ci", "CI", "ci"),
  },
  activeGroup: "diff-group",
};

const DEBUG: SerializedDockview = {
  grid: {
    height: 1000,
    width: 1600,
    orientation: Orientation.HORIZONTAL,
    root: {
      type: "branch",
      data: [
        {
          type: "leaf",
          size: 220,
          data: { views: ["tree"], activeView: "tree", id: "tree-group" },
        },
        {
          type: "branch",
          size: 1380,
          data: [
            {
              type: "leaf",
              size: 600,
              data: { views: ["editor"], activeView: "editor", id: "editor-group" },
            },
            {
              type: "leaf",
              size: 400,
              data: {
                views: ["terminal", "preview"],
                activeView: "terminal",
                id: "bottom-group",
              },
            },
          ],
        },
      ],
    },
  },
  panels: {
    tree: treePanel(),
    editor: placeholder("editor", "Editor", "editor"),
    terminal: placeholder("terminal", "Terminal", "terminal"),
    preview: placeholder("preview", "Preview", "preview"),
  },
  activeGroup: "editor-group",
};

export const PRESETS: Record<LayoutPreset, SerializedDockview> = {
  focus: FOCUS,
  review: REVIEW,
  debug: DEBUG,
  custom: FOCUS, // initial baseline; the user reshapes it via drag
};

export const ALL_PRESETS: LayoutPreset[] = ["focus", "review", "debug", "custom"];
