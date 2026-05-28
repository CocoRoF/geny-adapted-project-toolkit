import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useI18n } from "@/app/providers/i18n-context";
import { usePaletteAction } from "@/app/usePaletteAction";
import { ChatPanel } from "@/chat/ChatPanel";
import { FileEditor } from "@/ide/Editor";
import { ActivityBar, type SideView } from "@/ide/shell/ActivityBar";
import { BottomPanel, type BottomTab } from "@/ide/shell/BottomPanel";
import { SidePanel } from "@/ide/shell/SidePanel";
import { SplitHandle } from "@/ide/shell/SplitHandle";
import { StatusBar } from "@/ide/shell/StatusBar";

interface Props {
  workspaceId: string;
  projectId: string;
  branch: string;
  workspaceStatus: string;
}

const STORAGE_KEY_PREFIX = "gapt.ide.shell";

interface LayoutState {
  sideView: SideView | null;
  sideWidth: number;
  bottomTab: BottomTab | null;
  bottomHeight: number;
  chatOpen: boolean;
  chatWidth: number;
}

const DEFAULT_LAYOUT: LayoutState = {
  sideView: "files",
  sideWidth: 260,
  bottomTab: null,
  bottomHeight: 240,
  chatOpen: true,
  chatWidth: 380,
};

/** Phase D.5 — Named layout presets selectable from the palette
 *  (Cmd/Ctrl+K). Each preset is a complete `LayoutState` so
 *  switching is a single set-state call. Operator-saved layouts
 *  (current behaviour: workspace localStorage entry) survive a
 *  preset switch — selecting "default" doesn't wipe the LS value
 *  for OTHER workspaces, only the current one's. */
const LAYOUT_PRESETS: Record<string, LayoutState> = {
  default: DEFAULT_LAYOUT,
  chat_focused: {
    sideView: null,
    sideWidth: 260,
    bottomTab: null,
    bottomHeight: 240,
    chatOpen: true,
    chatWidth: 560,
  },
  debug: {
    sideView: "files",
    sideWidth: 240,
    bottomTab: "terminal",
    bottomHeight: 280,
    chatOpen: true,
    chatWidth: 360,
  },
  minimal: {
    sideView: null,
    sideWidth: 260,
    bottomTab: null,
    bottomHeight: 240,
    chatOpen: false,
    chatWidth: 380,
  },
};

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
export function IdeShell({ workspaceId, projectId, branch, workspaceStatus }: Props) {
  const navigate = useNavigate();
  const { t } = useI18n();
  const [layout, setLayout] = useState<LayoutState>(() => readStored(workspaceId));
  const [openFile, setOpenFile] = useState<string | null>(null);

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
  const setChatWidth = useCallback((n: number) => {
    setLayout((s) => ({ ...s, chatWidth: n }));
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
      } else if (code === "KeyA") {
        e.preventDefault();
        setChatOpen(!layout.chatOpen);
      } else if (code === "KeyD") {
        e.preventDefault();
        setBottomTab(layout.bottomTab === "diff" ? null : "diff");
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
                onOpenFile={(p) => setOpenFile(p)}
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

        {/* Editor column — fills the remaining width */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <div className="min-h-0 flex-1 overflow-hidden bg-bg">
            <FileEditor workspaceId={workspaceId} openPath={openFile} />
          </div>
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

        {layout.chatOpen ? (
          <>
            <SplitHandle
              axis="horizontal"
              value={layout.chatWidth}
              onChange={setChatWidth}
              min={280}
              max={640}
              invert
            />
            <div
              className="h-full shrink-0 overflow-hidden border-l border-border bg-bg-elevated"
              style={{ width: `${layout.chatWidth}px` }}
            >
              <ChatPanel projectId={projectId} workspaceId={workspaceId} />
            </div>
          </>
        ) : null}
      </div>

      <StatusBar
        branch={branch}
        workspaceStatus={workspaceStatus}
        openFile={openFile}
        onToggleTerminal={onToggleTerminal}
      />
    </div>
  );
}
