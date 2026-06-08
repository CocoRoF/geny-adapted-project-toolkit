import { X } from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import { FileEditor } from "@/ide/Editor";
import { FileDiffView } from "@/ide/FileDiffView";
import { PreviewTabContent } from "@/ide/PreviewTabContent";
import { EditorTabBar } from "@/ide/shell/EditorTabBar";
import type { EditorTab } from "@/ide/shell/IdeShell";

interface Props {
  workspaceId: string;
  tabs: EditorTab[];
  activeTabId: string | null;
  onActivateTab: (id: string) => void;
  onCloseTab: (id: string) => void;
  /** Hide the entire editor column (the X in the header). Tabs are
   *  preserved on the parent so re-opening the column shows the same
   *  open set. */
  onCloseColumn: () => void;
}

/** Phase N.3 — wraps the editor column with a VSCode-style tab bar.
 *
 * Layout: tab strip on top, active tab body below. Three tab kinds
 * are rendered by different components:
 *   - `file`    → Monaco editor (`FileEditor`)
 *   - `diff`    → working-tree-vs-HEAD diff (`FileDiffView`)
 *   - `preview` → embedded iframe (`PreviewTabContent`)
 *
 * Preview tabs are always mounted with `display:none` for inactive
 * ones so the iframe state (cookies, JS heap, scroll, route) survives
 * tab switches — the whole point of the embedded preview. File / diff
 * tabs use the same hide-don't-unmount strategy so Monaco's undo /
 * cursor / dirty buffer survive too; the user paid a debounce cost
 * to autosave and shouldn't lose anything just because they peeked
 * at another tab.
 *
 * An empty `tabs` array shows a short hint instead of a centered
 * placeholder so chat / sidebar stay the visual focus. The `×` in
 * the header collapses the whole column. */
export function EditorArea({
  workspaceId,
  tabs,
  activeTabId,
  onActivateTab,
  onCloseTab,
  onCloseColumn,
}: Props) {
  const { t } = useI18n();

  if (tabs.length === 0) {
    return (
      <section className="flex h-full min-h-0 flex-col bg-bg">
        <header className="flex h-8 shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-2 text-[12px]">
          <span className="flex-1 text-fg-subtle">
            {t("ide.editor.area.no_selection")}
          </span>
          <button
            type="button"
            onClick={onCloseColumn}
            title={t("ide.editor.area.close")}
            aria-label={t("ide.editor.area.close")}
            className="grid h-6 w-6 place-items-center rounded text-fg-subtle hover:bg-bg-subtle hover:text-fg"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-hidden bg-bg px-4 pt-4 text-[11.5px] text-fg-subtle">
          {t("ide.editor.area.empty_hint")}
        </div>
      </section>
    );
  }

  return (
    <section className="flex h-full min-h-0 flex-col bg-bg">
      <div className="flex shrink-0 items-stretch border-b border-border bg-bg-elevated">
        <div className="min-w-0 flex-1">
          <EditorTabBar
            tabs={tabs}
            activeTabId={activeTabId}
            onActivate={onActivateTab}
            onClose={onCloseTab}
          />
        </div>
        <button
          type="button"
          onClick={onCloseColumn}
          title={t("ide.editor.area.close")}
          aria-label={t("ide.editor.area.close")}
          className="grid h-8 w-8 shrink-0 place-items-center border-l border-border text-fg-subtle hover:bg-bg-subtle hover:text-fg"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="relative min-h-0 flex-1 overflow-hidden bg-bg">
        {tabs.map((tab) => {
          const active = tab.id === activeTabId;
          // Visible tab fills the column; inactive ones stay mounted
          // beneath it with display:none so their state survives a
          // switch. `inset-0` lays each child over the same area.
          return (
            <div
              key={tab.id}
              role="tabpanel"
              aria-hidden={!active}
              className="absolute inset-0"
              style={{ display: active ? "block" : "none" }}
            >
              <TabBody workspaceId={workspaceId} tab={tab} />
            </div>
          );
        })}
      </div>
    </section>
  );
}

function TabBody({
  workspaceId,
  tab,
}: {
  workspaceId: string;
  tab: EditorTab;
}) {
  if (tab.kind === "preview") {
    return <PreviewTabContent initialUrl={tab.url} />;
  }
  if (tab.kind === "diff") {
    return <FileDiffView workspaceId={workspaceId} path={tab.path} />;
  }
  return <FileEditor workspaceId={workspaceId} openPath={tab.path} />;
}
