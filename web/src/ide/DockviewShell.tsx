import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type DockviewApi,
  DockviewReact,
  type IDockviewPanelProps,
  type SerializedDockview,
} from "dockview";

import { useI18n } from "@/app/providers/i18n-context";
import { usePaletteAction } from "@/app/usePaletteAction";
import { EditorBus, EditorBusContext } from "@/ide/editor-store";
import { ALL_PRESETS, type LayoutPreset, PRESETS } from "@/ide/layouts";
import {
  AuditPanelDock,
  ChatPanelDock,
  CiPanelDock,
  CostPanelDock,
  EditorPanel,
  FileTreePanel,
  PanelPlaceholder,
  PreviewPanelDock,
} from "@/ide/panels";

import "dockview/dist/styles/dockview.css";

const STORAGE_KEY_PREFIX = "gapt.ide.layout";

function storageKey(workspaceId: string): string {
  return `${STORAGE_KEY_PREFIX}.${workspaceId}`;
}

interface StoredLayout {
  preset: LayoutPreset;
  customSnapshot?: SerializedDockview;
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
  preview: (props: IDockviewPanelProps<{ workspaceId: string }>) => <PreviewPanelDock {...props} />,
  audit: (props: IDockviewPanelProps<{ projectId: string }>) => <AuditPanelDock {...props} />,
  ci: (props: IDockviewPanelProps<{ projectId: string }>) => <CiPanelDock {...props} />,
  cost: (props: IDockviewPanelProps<Record<string, never>>) => <CostPanelDock {...props} />,
};

const HYDRATED_PANEL_KINDS = new Set(["tree", "editor", "chat", "preview", "audit", "ci", "cost"]);

interface Props {
  workspaceId: string;
  projectId: string;
}

/** Full IDE shell: dockview component + preset switcher. Real panel
 * implementations land in Cycles 3.4–3.10; today every leaf renders
 * `<PanelPlaceholder>`. */
export function DockviewShell({ workspaceId, projectId }: Props) {
  const { t } = useI18n();
  const initial = useMemo<StoredLayout>(
    () => readStored(workspaceId) ?? { preset: "focus" },
    [workspaceId],
  );
  const [preset, setPreset] = useState<LayoutPreset>(initial.preset);
  const apiRef = useRef<DockviewApi | null>(null);
  // Each shell instance owns its own bus — multiple workspaces in
  // the same browser session don't cross-talk.
  const editorBus = useMemo(() => new EditorBus(), []);

  // Register the layout presets as palette actions while this shell
  // is mounted. Plan §3.11 calls for Ctrl+Alt+1..4 shortcuts as
  // well — registered alongside.
  usePaletteAction({
    id: "ide.layout.focus",
    title: t("ide.layout.focus"),
    section: t("palette.section.layout"),
    shortcut: "⌃⌥1",
    run: () => setPreset("focus"),
  });
  usePaletteAction({
    id: "ide.layout.review",
    title: t("ide.layout.review"),
    section: t("palette.section.layout"),
    shortcut: "⌃⌥2",
    run: () => setPreset("review"),
  });
  usePaletteAction({
    id: "ide.layout.debug",
    title: t("ide.layout.debug"),
    section: t("palette.section.layout"),
    shortcut: "⌃⌥3",
    run: () => setPreset("debug"),
  });
  usePaletteAction({
    id: "ide.layout.custom",
    title: t("ide.layout.custom"),
    section: t("palette.section.layout"),
    shortcut: "⌃⌥4",
    run: () => setPreset("custom"),
  });

  // Ctrl+Alt+1..4 maps to the four presets.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!e.ctrlKey || !e.altKey) return;
      const idx = ["1", "2", "3", "4"].indexOf(e.key);
      if (idx < 0) return;
      e.preventDefault();
      setPreset(ALL_PRESETS[idx] ?? "focus");
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const loadPreset = useCallback(
    (next: LayoutPreset) => {
      const api = apiRef.current;
      if (!api) return;
      const stored = readStored(workspaceId);
      const layout =
        next === "custom" && stored?.customSnapshot ? stored.customSnapshot : PRESETS[next];
      // Inject the live workspaceId into every panel that needs it
      // (`tree`, `editor`) — the layout snapshot was authored with a
      // blank id placeholder.
      const hydrated = {
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
      api.fromJSON(hydrated);
    },
    [workspaceId, projectId],
  );

  // Apply preset whenever it changes (after the dockview has mounted).
  useEffect(() => {
    loadPreset(preset);
    writeStored(workspaceId, {
      preset,
      // Preserve any previously captured custom snapshot.
      ...(readStored(workspaceId)?.customSnapshot
        ? { customSnapshot: readStored(workspaceId)!.customSnapshot }
        : {}),
    });
  }, [preset, workspaceId, loadPreset]);

  function onReady(event: { api: DockviewApi }): void {
    apiRef.current = event.api;
    loadPreset(preset);

    // Persist user-driven changes to the custom snapshot so the
    // `custom` preset survives reload.
    event.api.onDidLayoutChange(() => {
      const snapshot = event.api.toJSON();
      writeStored(workspaceId, { preset: "custom", customSnapshot: snapshot });
      setPreset((current) => (current === "custom" ? current : "custom"));
    });
  }

  return (
    <EditorBusContext.Provider value={editorBus}>
      <div className="ide-shell">
        <nav className="ide-shell-toolbar" aria-label="layout presets">
          {ALL_PRESETS.map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={preset === p}
              onClick={() => setPreset(p)}
              className={preset === p ? "is-active" : undefined}
            >
              {t(`ide.layout.${p}`)}
            </button>
          ))}
          <button
            type="button"
            onClick={() => {
              window.localStorage.removeItem(storageKey(workspaceId));
              setPreset("focus");
              loadPreset("focus");
            }}
            className="ide-shell-reset"
          >
            {t("ide.layout.reset")}
          </button>
        </nav>
        <div className="ide-shell-body">
          <DockviewReact components={components} onReady={onReady} />
        </div>
      </div>
    </EditorBusContext.Provider>
  );
}
