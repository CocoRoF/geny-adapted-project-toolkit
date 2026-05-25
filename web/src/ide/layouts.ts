import { Orientation, type SerializedDockview } from "dockview";

/** Single baseline IDE layout — Tree | Editor | Chat.
 *
 * The previous preset library (Focus / Review / Debug / Custom) is
 * gone: switching presets reshuffled the workspace on every click and
 * the user was correct that it was friction without payoff. Everything
 * else (Terminal / Diff / Audit / etc.) is opened on demand via the
 * toolbar buttons or keyboard shortcuts driven by `DockviewShell`,
 * not by swapping the whole snapshot. */

export const TREE_ID = "tree";
export const EDITOR_ID = "editor";
export const CHAT_ID = "chat";
export const TERMINAL_ID = "terminal";
export const DIFF_ID = "diff";
export const ENV_ID = "env";
export const TESTS_ID = "tests";
export const GIT_ID = "git";

export const EDITOR_GROUP_ID = "editor-group";
export const TREE_GROUP_ID = "tree-group";
export const CHAT_GROUP_ID = "chat-group";

/** Hydration-time replacement happens in `DockviewShell` — `params`
 * here is just a placeholder so the serialized snapshot validates. */
function treePanel() {
  return {
    id: TREE_ID,
    contentComponent: "tree",
    title: "Files",
    params: { workspaceId: "", kind: "tree" },
  };
}

function editorPanel() {
  return {
    id: EDITOR_ID,
    contentComponent: "editor",
    title: "Editor",
    params: { workspaceId: "", kind: "editor" },
  };
}

function chatPanel() {
  return {
    id: CHAT_ID,
    contentComponent: "chat",
    title: "Chat",
    params: { workspaceId: "", projectId: "", kind: "chat" },
  };
}

/** The single baseline layout the IDE loads on first visit + after
 * the user resets. Three groups left → right: file tree, editor,
 * chat. Terminal / Diff get inserted on demand into their own groups
 * via `api.addPanel`. */
export const IDE_BASELINE: SerializedDockview = {
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
          data: { views: [TREE_ID], activeView: TREE_ID, id: TREE_GROUP_ID },
        },
        {
          type: "leaf",
          size: 880,
          data: { views: [EDITOR_ID], activeView: EDITOR_ID, id: EDITOR_GROUP_ID },
        },
        {
          type: "leaf",
          size: 500,
          data: { views: [CHAT_ID], activeView: CHAT_ID, id: CHAT_GROUP_ID },
        },
      ],
    },
  },
  panels: {
    [TREE_ID]: treePanel(),
    [EDITOR_ID]: editorPanel(),
    [CHAT_ID]: chatPanel(),
  },
  activeGroup: EDITOR_GROUP_ID,
};
