import { TerminalSquare, X } from "lucide-react";

import { TerminalPanel } from "@/ide/TerminalPanel";

/** Phase F — collapsed to a single tab.
 *
 * The legacy "Diff" tab and `DiffPanel` component were retired:
 * the source-control sidebar now hands per-file diffs to the
 * editor column (`FileDiffView` inside `EditorArea`), which is
 * what VSCode does. Keeping the bottom panel single-tab simplifies
 * the chrome — if a second tab ever lands we re-introduce the
 * union and the strip.
 */
export type BottomTab = "terminal";

interface Props {
  tab: BottomTab;
  onTab: (next: BottomTab) => void;
  onClose: () => void;
  workspaceId: string;
}

export function BottomPanel({ tab: _tab, onTab: _onTab, onClose, workspaceId }: Props) {
  return (
    <section
      data-panel-kind="bottom"
      className="flex h-full min-h-0 flex-col border-t border-border bg-bg-elevated"
    >
      <header className="flex h-8 shrink-0 items-center gap-1 border-b border-border px-2 text-[12px]">
        <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-fg">
          <TerminalSquare className="h-3.5 w-3.5" strokeWidth={1.5} />
          Terminal
        </span>
        <button
          type="button"
          onClick={onClose}
          title="Close panel"
          className="ml-auto grid h-6 w-6 place-items-center rounded text-fg-muted hover:bg-surface-hover hover:text-fg"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-hidden">
        <TerminalPanel workspaceId={workspaceId} />
      </div>
    </section>
  );
}
