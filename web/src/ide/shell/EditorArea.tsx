import { FileDiff, FileText, X } from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import { FileEditor } from "@/ide/Editor";
import { FileDiffView } from "@/ide/FileDiffView";
import type { EditorView } from "@/ide/shell/IdeShell";

interface Props {
  workspaceId: string;
  view: EditorView | null;
  onClose: () => void;
}

/** Phase F — wraps the editor column.
 *
 * Header carries a VSCode-style filename pill + a single X button
 * that hides the entire column (Chat / SidePanel grow to fill the
 * row). When no file or diff is open, the body shows a small
 * top-aligned hint instead of a centered placeholder that dominates
 * the screen.
 *
 * Single dispatch point for what kind of view is showing:
 *   - `file` → Monaco-backed editor
 *   - `diff` → unified working-tree-vs-HEAD diff for the file
 *
 * The two views never co-exist; clicking a file replaces a diff and
 * vice versa. That matches VSCode's main pane semantics. */
export function EditorArea({ workspaceId, view, onClose }: Props) {
  const { t } = useI18n();

  return (
    <section className="flex h-full min-h-0 flex-col bg-bg">
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-2 text-[12px]">
        {view ? (
          <>
            {view.kind === "diff" ? (
              <FileDiff className="h-3.5 w-3.5 shrink-0 text-accent" strokeWidth={1.5} />
            ) : (
              <FileText className="h-3.5 w-3.5 shrink-0 text-fg-muted" strokeWidth={1.5} />
            )}
            <span className="flex-1 truncate font-mono text-fg" title={view.path}>
              {view.path}
              {view.kind === "diff" ? (
                <span className="ml-1.5 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-accent">
                  diff
                </span>
              ) : null}
            </span>
          </>
        ) : (
          <span className="flex-1 text-fg-subtle">{t("ide.editor.area.no_selection")}</span>
        )}
        <button
          type="button"
          onClick={onClose}
          title={t("ide.editor.area.close")}
          aria-label={t("ide.editor.area.close")}
          className="grid h-6 w-6 place-items-center rounded text-fg-subtle hover:bg-bg-subtle hover:text-fg"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-hidden bg-bg">
        {view === null ? (
          <div className="px-4 pt-4 text-[11.5px] text-fg-subtle">
            {t("ide.editor.area.empty_hint")}
          </div>
        ) : view.kind === "diff" ? (
          <FileDiffView workspaceId={workspaceId} path={view.path} />
        ) : (
          <FileEditor workspaceId={workspaceId} openPath={view.path} />
        )}
      </div>
    </section>
  );
}
