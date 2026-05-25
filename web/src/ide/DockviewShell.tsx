import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  type DockviewApi,
  DockviewReact,
  type IDockviewPanelProps,
  type SerializedDockview,
} from "dockview";
import { FlaskConical, GitBranch, GitCompare, KeyRound, RotateCcw, TerminalSquare } from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import { usePaletteAction } from "@/app/usePaletteAction";
import { EditorBus, EditorBusContext } from "@/ide/editor-store";
import {
  DIFF_ID,
  EDITOR_GROUP_ID,
  EDITOR_ID,
  ENV_ID,
  GIT_ID,
  IDE_BASELINE,
  TERMINAL_ID,
  TESTS_ID,
} from "@/ide/layouts";
import {
  ChatPanelDock,
  DiffPanelDock,
  EditorPanel,
  EnvPanelDock,
  FileTreePanel,
  GitPanelDock,
  PanelPlaceholder,
  TerminalPanelDock,
  TestsPanelDock,
} from "@/ide/panels";

import "dockview/dist/styles/dockview.css";

const STORAGE_KEY_PREFIX = "gapt.ide.layout";

function storageKey(workspaceId: string): string {
  return `${STORAGE_KEY_PREFIX}.${workspaceId}`;
}

interface StoredLayout {
  snapshot?: SerializedDockview;
}

function readStored(workspaceId: string): StoredLayout | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(storageKey(workspaceId));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StoredLayout;
  } catch {
    return null;
  }
}

function writeStored(workspaceId: string, value: StoredLayout): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey(workspaceId), JSON.stringify(value));
}

const components = {
  placeholder: (props: IDockviewPanelProps<{ kind: string }>) => <PanelPlaceholder {...props} />,
  tree: (props: IDockviewPanelProps<{ workspaceId: string }>) => <FileTreePanel {...props} />,
  editor: (props: IDockviewPanelProps<{ workspaceId: string }>) => <EditorPanel {...props} />,
  chat: (props: IDockviewPanelProps<{ workspaceId: string; projectId: string }>) => (
    <ChatPanelDock {...props} />
  ),
  diff: (props: IDockviewPanelProps<{ workspaceId: string }>) => <DiffPanelDock {...props} />,
  terminal: (props: IDockviewPanelProps<{ workspaceId: string }>) => <TerminalPanelDock {...props} />,
  env: (props: IDockviewPanelProps<{ workspaceId: string }>) => <EnvPanelDock {...props} />,
  tests: (props: IDockviewPanelProps<{ workspaceId: string }>) => <TestsPanelDock {...props} />,
  git: (props: IDockviewPanelProps<{ workspaceId: string }>) => <GitPanelDock {...props} />,
};

const HYDRATED_PANEL_KINDS = new Set([
  "tree", "editor", "chat", "diff", "terminal", "env", "tests", "git",
]);

interface Props {
  workspaceId: string;
  projectId: string;
}

/** IDE shell. One baseline layout (Tree | Editor | Chat); auxiliary
 * panels (Terminal, Diff) toggle in/out via the toolbar or keyboard
 * shortcuts. The user's drag-and-resize state is persisted per
 * workspace so reload picks up where they left off. */
export function DockviewShell({ workspaceId, projectId }: Props) {
  const { t } = useI18n();
  const apiRef = useRef<DockviewApi | null>(null);
  // Each shell instance owns its own bus — multiple workspaces in
  // the same browser session don't cross-talk.
  const editorBus = useMemo(() => new EditorBus(), []);

  /** Inject the live workspaceId/projectId into a serialized snapshot's
   * panel params so the components don't render against blank ids. */
  const hydrate = useCallback(
    (layout: SerializedDockview): SerializedDockview => {
      return {
        ...layout,
        panels: Object.fromEntries(
          Object.entries(layout.panels).map(([id, panel]) => {
            if (
              typeof panel.contentComponent !== "string" ||
              !HYDRATED_PANEL_KINDS.has(panel.contentComponent)
            ) {
              return [id, panel];
            }
            return [
              id,
              {
                ...panel,
                params: { ...panel.params, workspaceId, projectId },
              },
            ];
          }),
        ),
      };
    },
    [workspaceId, projectId],
  );

  const loadBaseline = useCallback(() => {
    const api = apiRef.current;
    if (!api) return;
    api.fromJSON(hydrate(IDE_BASELINE));
  }, [hydrate]);

  /** Add (or focus, if already mounted) one of the toggle-able panels.
   * Terminal lands as a horizontal split below the editor group;
   * Diff + Env land as extra tabs on the editor group. */
  const togglePanel = useCallback(
    (
      id:
        | typeof TERMINAL_ID
        | typeof DIFF_ID
        | typeof ENV_ID
        | typeof TESTS_ID
        | typeof GIT_ID,
    ) => {
      const api = apiRef.current;
      if (!api) return;
      const existing = api.getPanel(id);
      if (existing) {
        existing.api.close();
        return;
      }
      if (id === TERMINAL_ID) {
        api.addPanel({
          id: TERMINAL_ID,
          component: "terminal",
          title: t("ide.panel.terminal"),
          params: { workspaceId, kind: "terminal" },
          position: { referenceGroup: EDITOR_GROUP_ID, direction: "below" },
        });
      } else if (id === DIFF_ID) {
        api.addPanel({
          id: DIFF_ID,
          component: "diff",
          title: t("ide.panel.diff"),
          params: { workspaceId, kind: "diff" },
          position: { referenceGroup: EDITOR_GROUP_ID, direction: "within" },
        });
      } else if (id === ENV_ID) {
        api.addPanel({
          id: ENV_ID,
          component: "env",
          title: t("ide.panel.env"),
          params: { workspaceId, kind: "env" },
          position: { referenceGroup: EDITOR_GROUP_ID, direction: "within" },
        });
      } else if (id === TESTS_ID) {
        api.addPanel({
          id: TESTS_ID,
          component: "tests",
          title: t("ide.panel.tests"),
          params: { workspaceId, kind: "tests" },
          position: { referenceGroup: EDITOR_GROUP_ID, direction: "below" },
        });
      } else if (id === GIT_ID) {
        api.addPanel({
          id: GIT_ID,
          component: "git",
          title: t("ide.panel.git"),
          params: { workspaceId, kind: "git" },
          position: { referenceGroup: EDITOR_GROUP_ID, direction: "within" },
        });
      }
    },
    [t, workspaceId],
  );

  // Keyboard shortcuts. Ctrl+`  → terminal (Cursor/VS Code parity).
  // Ctrl+Shift+G → diff. Ctrl+Shift+R → reset layout.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && !e.altKey && !e.metaKey && (e.key === "`" || e.code === "Backquote")) {
        e.preventDefault();
        togglePanel(TERMINAL_ID);
        return;
      }
      if (e.ctrlKey && e.shiftKey && (e.key === "G" || e.key === "g")) {
        e.preventDefault();
        togglePanel(DIFF_ID);
        return;
      }
      if (e.ctrlKey && e.shiftKey && (e.key === "E" || e.key === "e")) {
        e.preventDefault();
        togglePanel(ENV_ID);
        return;
      }
      if (e.ctrlKey && e.shiftKey && (e.key === "T" || e.key === "t")) {
        e.preventDefault();
        togglePanel(TESTS_ID);
        return;
      }
      if (e.ctrlKey && e.shiftKey && (e.key === "S" || e.key === "s")) {
        e.preventDefault();
        togglePanel(GIT_ID);
        return;
      }
      if (e.ctrlKey && e.shiftKey && (e.key === "R" || e.key === "r")) {
        e.preventDefault();
        loadBaseline();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [togglePanel, loadBaseline]);

  usePaletteAction({
    id: "ide.terminal.toggle",
    title: t("ide.toolbar.terminal"),
    section: t("palette.section.layout"),
    shortcut: "⌃`",
    run: () => togglePanel(TERMINAL_ID),
  });
  usePaletteAction({
    id: "ide.diff.toggle",
    title: t("ide.toolbar.diff"),
    section: t("palette.section.layout"),
    shortcut: "⌃⇧G",
    run: () => togglePanel(DIFF_ID),
  });
  usePaletteAction({
    id: "ide.env.toggle",
    title: t("ide.toolbar.env"),
    section: t("palette.section.layout"),
    shortcut: "⌃⇧E",
    run: () => togglePanel(ENV_ID),
  });
  usePaletteAction({
    id: "ide.tests.toggle",
    title: t("ide.toolbar.tests"),
    section: t("palette.section.layout"),
    shortcut: "⌃⇧T",
    run: () => togglePanel(TESTS_ID),
  });
  usePaletteAction({
    id: "ide.git.toggle",
    title: t("ide.toolbar.git"),
    section: t("palette.section.layout"),
    shortcut: "⌃⇧S",
    run: () => togglePanel(GIT_ID),
  });
  usePaletteAction({
    id: "ide.layout.reset",
    title: t("ide.layout.reset"),
    section: t("palette.section.layout"),
    shortcut: "⌃⇧R",
    run: () => loadBaseline(),
  });

  function onReady(event: { api: DockviewApi }): void {
    apiRef.current = event.api;
    // Restore the user's previously-shaped layout when present; fall
    // back to the baseline. If the stored snapshot is missing the
    // editor panel (somehow corrupted / closed), treat as fresh.
    const stored = readStored(workspaceId);
    const candidate = stored?.snapshot;
    const usable =
      candidate &&
      candidate.panels &&
      Object.keys(candidate.panels).some((id) => id === EDITOR_ID);
    event.api.fromJSON(hydrate(usable ? (candidate as SerializedDockview) : IDE_BASELINE));

    // Save dragged / toggled state so reload survives.
    event.api.onDidLayoutChange(() => {
      writeStored(workspaceId, { snapshot: event.api.toJSON() });
    });
  }

  return (
    <EditorBusContext.Provider value={editorBus}>
      <div className="flex h-full flex-col">
        <Toolbar
          onToggleTerminal={() => togglePanel(TERMINAL_ID)}
          onToggleDiff={() => togglePanel(DIFF_ID)}
          onToggleEnv={() => togglePanel(ENV_ID)}
          onToggleTests={() => togglePanel(TESTS_ID)}
          onToggleGit={() => togglePanel(GIT_ID)}
          onReset={() => {
            window.localStorage.removeItem(storageKey(workspaceId));
            loadBaseline();
          }}
        />
        <div className="flex-1 overflow-hidden">
          <DockviewReact
            components={components}
            onReady={onReady}
            className="dockview-theme-abyss"
          />
        </div>
      </div>
    </EditorBusContext.Provider>
  );
}

function Toolbar({
  onToggleTerminal,
  onToggleDiff,
  onToggleEnv,
  onToggleTests,
  onToggleGit,
  onReset,
}: {
  onToggleTerminal: () => void;
  onToggleDiff: () => void;
  onToggleEnv: () => void;
  onToggleTests: () => void;
  onToggleGit: () => void;
  onReset: () => void;
}) {
  const { t } = useI18n();
  const btnCls =
    "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12px] font-medium text-fg-muted hover:bg-surface-hover hover:text-fg";
  return (
    <nav
      className="flex shrink-0 items-center gap-1 border-b border-border bg-bg-elevated px-3 py-1.5"
      aria-label="ide toolbar"
    >
      <button type="button" className={btnCls} onClick={onToggleTerminal} title="Ctrl+`">
        <TerminalSquare className="h-3.5 w-3.5" />
        {t("ide.toolbar.terminal")}
        <kbd className="ml-1 hidden rounded bg-bg px-1 text-[10px] text-fg-subtle sm:inline">
          Ctrl+`
        </kbd>
      </button>
      <button type="button" className={btnCls} onClick={onToggleDiff} title="Ctrl+Shift+G">
        <GitCompare className="h-3.5 w-3.5" />
        {t("ide.toolbar.diff")}
        <kbd className="ml-1 hidden rounded bg-bg px-1 text-[10px] text-fg-subtle sm:inline">
          Ctrl+Shift+G
        </kbd>
      </button>
      <button type="button" className={btnCls} onClick={onToggleEnv} title="Ctrl+Shift+E">
        <KeyRound className="h-3.5 w-3.5" />
        {t("ide.toolbar.env")}
        <kbd className="ml-1 hidden rounded bg-bg px-1 text-[10px] text-fg-subtle sm:inline">
          Ctrl+Shift+E
        </kbd>
      </button>
      <button type="button" className={btnCls} onClick={onToggleTests} title="Ctrl+Shift+T">
        <FlaskConical className="h-3.5 w-3.5" />
        {t("ide.toolbar.tests")}
        <kbd className="ml-1 hidden rounded bg-bg px-1 text-[10px] text-fg-subtle sm:inline">
          Ctrl+Shift+T
        </kbd>
      </button>
      <button type="button" className={btnCls} onClick={onToggleGit} title="Ctrl+Shift+S">
        <GitBranch className="h-3.5 w-3.5" />
        {t("ide.toolbar.git")}
        <kbd className="ml-1 hidden rounded bg-bg px-1 text-[10px] text-fg-subtle sm:inline">
          Ctrl+Shift+S
        </kbd>
      </button>
      <button
        type="button"
        className={`${btnCls} ml-auto`}
        onClick={onReset}
        title="Ctrl+Shift+R"
      >
        <RotateCcw className="h-3.5 w-3.5" />
        {t("ide.layout.reset")}
      </button>
    </nav>
  );
}
