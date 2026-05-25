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
    // `loadPreset` — `params` here is a placeholder we never persist.
    params: { workspaceId: "", kind: "tree" },
  };
}

function editorPanel(id: string = "editor", title: string = "Editor") {
  return {
    id,
    contentComponent: "editor",
    title,
    params: { workspaceId: "", kind: "editor" },
  };
}

function chatPanel(id: string = "chat", title: string = "Chat") {
  return {
    id,
    contentComponent: "chat",
    title,
    params: { workspaceId: "", projectId: "", kind: "chat" },
  };
}

function previewPanel(id: string = "preview", title: string = "Preview") {
  return {
    id,
    contentComponent: "preview",
    title,
    params: { workspaceId: "", kind: "preview" },
  };
}

function auditPanel(id: string = "audit", title: string = "Audit") {
  return {
    id,
    contentComponent: "audit",
    title,
    params: { projectId: "", kind: "audit" },
  };
}

function diffPanel(id: string = "diff", title: string = "Diff") {
  return {
    id,
    contentComponent: "diff",
    title,
    params: { workspaceId: "", kind: "diff" },
  };
}

function terminalPanel(id: string = "terminal", title: string = "Terminal") {
  return {
    id,
    contentComponent: "terminal",
    title,
    params: { workspaceId: "", kind: "terminal" },
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
    editor: editorPanel(),
    chat: chatPanel(),
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
              data: { views: ["audit"], activeView: "audit", id: "audit-group" },
            },
          ],
        },
      ],
    },
  },
  panels: {
    tree: treePanel(),
    diff: diffPanel(),
    chat: chatPanel(),
    audit: auditPanel(),
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
    editor: editorPanel(),
    terminal: terminalPanel(),
    preview: previewPanel(),
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
