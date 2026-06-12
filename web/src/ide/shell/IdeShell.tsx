import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useI18n } from "@/app/providers/i18n-context";
import { usePaletteAction } from "@/app/usePaletteAction";
import { ChatPanel } from "@/chat/ChatPanel";
import { ActivityBar, type SideView } from "@/ide/shell/ActivityBar";
import { BottomPanel, type BottomTab } from "@/ide/shell/BottomPanel";
import { EditorArea } from "@/ide/shell/EditorArea";
import { type EditorTab, tabIdFor } from "@/ide/shell/editor-tabs";
import {
  DEFAULT_LAYOUT,
  LAYOUT_PRESETS,
  type LayoutState,
} from "@/ide/shell/layouts";
import { SidePanel } from "@/ide/shell/SidePanel";
import { SplitHandle } from "@/ide/shell/SplitHandle";
import { StatusBar } from "@/ide/shell/StatusBar";

interface Props {
  workspaceId: string;
  projectId: string;
  /** Phase N.5 — workspace identity is now ``name``. Per-repo branches
   *  flow through GitPanel via its repo selector + status fetch. */
  name: string;
  workspaceStatus: string;
}

const STORAGE_KEY_PREFIX = "gapt.ide.shell";

function storageKey(workspaceId: string): string {
  return `${STORAGE_KEY_PREFIX}.${workspaceId}`;
}

function readStored(workspaceId: string): LayoutState {
  if (typeof window === "undefined") return DEFAULT_LAYOUT;
  const raw = window.localStorage.getItem(storageKey(workspaceId));
  if (!raw) return DEFAULT_LAYOUT;
  try {
    return { ...DEFAULT_LAYOUT, ...(JSON.parse(raw) as Partial<LayoutState>) };
  } catch {
    return DEFAULT_LAYOUT;
  }
}

function writeStored(workspaceId: string, state: LayoutState): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey(workspaceId), JSON.stringify(state));
}

/** VSCode-style IDE shell.
 *
 * Layout (left → right): ActivityBar │ SidePanel │ EditorColumn │ Chat
 * EditorColumn splits top → bottom into Editor / BottomPanel.
 *
 * Each split has a `SplitHandle` for resize; the activity-bar items
 * + chat icon toggle their respective panels in/out. Layout state
 * persists in localStorage per workspace. */
export function IdeShell({ workspaceId, projectId, name, workspaceStatus }: Props) {
  const navigate = useNavigate();
  const { t } = useI18n();
  const [layout, setLayout] = useState<LayoutState>(() => readStored(workspaceId));
  // Phase N.3 — Editor column is now multi-tab (VSCode-style). `tabs`
  // is the open set in insertion order; `activeTabId` is which one's
  // body is visible. Empty `tabs` renders a placeholder; closing the
  // last tab also collapses the column via `editorOpen=false`.
  const [tabs, setTabs] = useState<EditorTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);

  /** Add a tab if one with this id doesn't already exist; activate
   * it either way. The editor column auto-opens so the user always
   * sees what they just clicked. */
  const openTab = useCallback((tab: EditorTab) => {
    setTabs((prev) =>
      prev.some((t) => t.id === tab.id) ? prev : [...prev, tab],
    );
    setActiveTabId(tab.id);
    setLayout((s) => (s.editorOpen ? s : { ...s, editorOpen: true }));
  }, []);

  const openFileInEditor = useCallback(
    (path: string) => {
      openTab({ id: tabIdFor("file", path), kind: "file", path });
    },
    [openTab],
  );
  const openDiffInEditor = useCallback(
    (path: string) => {
      openTab({ id: tabIdFor("diff", path), kind: "diff", path });
    },
    [openTab],
  );
  const openPreviewTab = useCallback(
    (url: string, label: string) => {
      openTab({ id: tabIdFor("preview", url), kind: "preview", url, label });
    },
    [openTab],
  );

  /** Close one tab; if it was active, fall back to the previous
   * sibling (or the next, if none). Closing the last tab keeps the
   * column open but with the placeholder so the user can still see
   * the close-all `×` and toggle terminal — VSCode parity. */
  const closeTab = useCallback((id: string) => {
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.id === id);
      if (idx < 0) return prev;
      const next = prev.filter((t) => t.id !== id);
      setActiveTabId((current) => {
        if (current !== id) return current;
        if (next.length === 0) return null;
        const fallback = next[Math.max(0, idx - 1)] ?? next[0];
        return fallback?.id ?? null;
      });
      return next;
    });
  }, []);

  /** Hide the editor column entirely (the `×` in the column header).
   * Tabs are preserved so re-opening shows the same set. */
  const closeEditorColumn = useCallback(() => {
    setLayout((s) => ({ ...s, editorOpen: false }));
  }, []);

  const activeTab = activeTabId
    ? tabs.find((t) => t.id === activeTabId) ?? null
    : null;

  // Phase D.5 — palette-driven layout presets. One usePaletteAction
  // per preset (the cmdk fuzzy filter handles ranking).
  usePaletteAction({
    id: "layout.preset.default",
    title: t("ide.layout.preset.default"),
    section: t("palette.section.layout"),
    keywords: ["layout", "preset", "default"],
    run: () => setLayout(LAYOUT_PRESETS.default),
  });
  usePaletteAction({
    id: "layout.preset.chat_focused",
    title: t("ide.layout.preset.chat_focused"),
    section: t("palette.section.layout"),
    keywords: ["layout", "preset", "chat", "focused"],
    run: () => setLayout(LAYOUT_PRESETS.chat_focused),
  });
  usePaletteAction({
    id: "layout.preset.debug",
    title: t("ide.layout.preset.debug"),
    section: t("palette.section.layout"),
    keywords: ["layout", "preset", "debug", "terminal"],
    run: () => setLayout(LAYOUT_PRESETS.debug),
  });
  usePaletteAction({
    id: "layout.preset.minimal",
    title: t("ide.layout.preset.minimal"),
    section: t("palette.section.layout"),
    keywords: ["layout", "preset", "minimal", "editor"],
    run: () => setLayout(LAYOUT_PRESETS.minimal),
  });

  // Persist layout to LS whenever it changes (debounced via state
  // batching — write happens on every effect tick which is rare).
  useEffect(() => {
    writeStored(workspaceId, layout);
  }, [workspaceId, layout]);

  const setSideView = useCallback((v: SideView | null) => {
    setLayout((s) => ({ ...s, sideView: v }));
  }, []);
  const setSideWidth = useCallback((n: number) => {
    setLayout((s) => ({ ...s, sideWidth: n }));
  }, []);
  const setBottomTab = useCallback((v: BottomTab | null) => {
    setLayout((s) => ({ ...s, bottomTab: v }));
  }, []);
  const setBottomHeight = useCallback((n: number) => {
    setLayout((s) => ({ ...s, bottomHeight: n }));
  }, []);
  const setChatOpen = useCallback((v: boolean) => {
    setLayout((s) => ({ ...s, chatOpen: v }));
  }, []);

  // Pop-out chat = MOVE, not copy (devtools-undock semantics). When
  // the panel reports a successful window.open we close the docked
  // column and hold the popup handle; a light poll watches for the
  // popup closing and brings the docked panel back. Manually
  // re-toggling chat (activity bar / Ctrl+Shift+A) while the popup
  // is open stops the watch — the user explicitly chose both.
  const chatPopupRef = useRef<Window | null>(null);
  const onChatPoppedOut = useCallback(
    (win: Window) => {
      chatPopupRef.current = win;
      setChatOpen(false);
    },
    [setChatOpen],
  );
  useEffect(() => {
    if (layout.chatOpen && chatPopupRef.current) {
      // Docked chat reopened while the popup is alive — release the
      // handle so closing the popup later doesn't force the layout.
      chatPopupRef.current = null;
    }
    const id = window.setInterval(() => {
      const win = chatPopupRef.current;
      if (win && win.closed) {
        chatPopupRef.current = null;
        setChatOpen(true);
      }
    }, 700);
    return () => window.clearInterval(id);
  }, [layout.chatOpen, setChatOpen]);
  const setChatWidth = useCallback((n: number) => {
    setLayout((s) => ({ ...s, chatWidth: n }));
  }, []);
  const setEditorOpen = useCallback((v: boolean) => {
    setLayout((s) => ({ ...s, editorOpen: v }));
  }, []);

  // Keyboard shortcuts — VSCode-ish parity where reasonable.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.altKey || e.metaKey) return;
      const code = e.code;
      const shift = e.shiftKey;
      if (code === "Backquote" && !shift) {
        e.preventDefault();
        setBottomTab(layout.bottomTab === "terminal" ? null : "terminal");
        return;
      }
      if (!shift) return;
      if (code === "KeyE") {
        e.preventDefault();
        setSideView(layout.sideView === "files" ? null : "files");
      } else if (code === "KeyF") {
        e.preventDefault();
        setSideView(layout.sideView === "search" ? null : "search");
      } else if (code === "KeyG") {
        e.preventDefault();
        setSideView(layout.sideView === "git" ? null : "git");
      } else if (code === "KeyT") {
        e.preventDefault();
        setSideView(layout.sideView === "tests" ? null : "tests");
      } else if (code === "KeyV") {
        e.preventDefault();
        setSideView(layout.sideView === "env" ? null : "env");
      } else if (code === "KeyS") {
        // Phase N.3 — Services sidebar (replaces the old top-level
        // "개발" tab). Doesn't clash with Monaco's Ctrl+S because that
        // command needs no Shift.
        e.preventDefault();
        setSideView(layout.sideView === "services" ? null : "services");
      } else if (code === "KeyA") {
        e.preventDefault();
        setChatOpen(!layout.chatOpen);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    layout.bottomTab,
    layout.chatOpen,
    layout.sideView,
    setBottomTab,
    setChatOpen,
    setSideView,
  ]);

  const onToggleTerminal = useCallback(() => {
    setBottomTab(layout.bottomTab === "terminal" ? null : "terminal");
  }, [layout.bottomTab, setBottomTab]);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-bg">
      {/* Main row: activity bar │ side │ editor column │ chat */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <ActivityBar
          active={layout.sideView}
          onSelect={setSideView}
          chatOpen={layout.chatOpen}
          onToggleChat={() => setChatOpen(!layout.chatOpen)}
          onOpenSettings={() => void navigate("/settings")}
        />

        {layout.sideView !== null ? (
          <>
            <div
              className="h-full shrink-0 overflow-hidden"
              style={{ width: `${layout.sideWidth}px` }}
            >
              <SidePanel
                view={layout.sideView}
                workspaceId={workspaceId}
                projectId={projectId}
                onOpenFile={openFileInEditor}
                onOpenDiff={openDiffInEditor}
                onOpenPreview={openPreviewTab}
              />
            </div>
            <SplitHandle
              axis="horizontal"
              value={layout.sideWidth}
              onChange={setSideWidth}
              min={180}
              max={520}
            />
          </>
        ) : null}

        {/* Editor column — fills the remaining width when open. When
            closed (operator clicked X on the editor header) the
            column disappears and the Chat panel grows to take the
            freed space. The bottom panel (terminal) lives inside
            this column, so closing the editor also closes the
            terminal — pick "Open editor" or hit Ctrl+\ to bring it
            back. */}
        {layout.editorOpen ? (
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <EditorArea
              workspaceId={workspaceId}
              tabs={tabs}
              activeTabId={activeTabId}
              onActivateTab={setActiveTabId}
              onCloseTab={closeTab}
              onCloseColumn={closeEditorColumn}
            />
            {layout.bottomTab !== null ? (
              <>
                <SplitHandle
                  axis="vertical"
                  value={layout.bottomHeight}
                  onChange={setBottomHeight}
                  min={120}
                  max={600}
                  invert
                />
                <div
                  className="shrink-0 overflow-hidden"
                  style={{ height: `${layout.bottomHeight}px` }}
                >
                  <BottomPanel
                    tab={layout.bottomTab}
                    onTab={setBottomTab}
                    onClose={() => setBottomTab(null)}
                    workspaceId={workspaceId}
                  />
                </div>
              </>
            ) : null}
          </div>
        ) : null}

        {layout.chatOpen ? (
          <>
            <SplitHandle
              axis="horizontal"
              value={layout.chatWidth}
              onChange={setChatWidth}
              // Chat can grow much wider now that the editor column
              // collapses. Lower bound stays ~280 for usable input.
              min={280}
              max={1400}
              invert
            />
            <div
              className={
                layout.editorOpen
                  ? // Phase N.3 — `border-border-strong` mirrors the
                    // sidebar's right edge so both seams against the
                    // editor look intentional + symmetric.
                    "h-full shrink-0 overflow-hidden border-l border-border-strong bg-bg-elevated"
                  : // Editor hidden → chat grows to fill the row instead
                    // of being pinned to a fixed pixel width.
                    "h-full min-w-0 flex-1 overflow-hidden border-l border-border-strong bg-bg-elevated"
              }
              style={layout.editorOpen ? { width: `${layout.chatWidth}px` } : undefined}
            >
              <ChatPanel
                projectId={projectId}
                workspaceId={workspaceId}
                onPoppedOut={onChatPoppedOut}
              />
            </div>
          </>
        ) : null}
      </div>

      <StatusBar
        name={name}
        workspaceStatus={workspaceStatus}
        openFile={activeTab?.kind === "file" ? activeTab.path : null}
        onToggleTerminal={onToggleTerminal}
        editorOpen={layout.editorOpen}
        onOpenEditor={() => setEditorOpen(true)}
      />
    </div>
  );
}
