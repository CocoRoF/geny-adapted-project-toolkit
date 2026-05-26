import { GitCompare, TerminalSquare, X } from "lucide-react";

import { DiffPanel } from "@/ide/DiffPanel";
import { TerminalPanel } from "@/ide/TerminalPanel";
import { cn } from "@/ui/cn";

export type BottomTab = "terminal" | "diff";

interface Props {
  tab: BottomTab;
  onTab: (next: BottomTab) => void;
  onClose: () => void;
  workspaceId: string;
}

const TABS: { id: BottomTab; label: string; Icon: typeof TerminalSquare }[] = [
  { id: "terminal", label: "Terminal", Icon: TerminalSquare },
  { id: "diff", label: "Diff", Icon: GitCompare },
];

/** Bottom panel — VSCode-style tab strip + content. Always height-
 * resizable from above. Closing collapses the whole panel. */
export function BottomPanel({ tab, onTab, onClose, workspaceId }: Props) {
  return (
    <section
      data-panel-kind="bottom"
      className="flex h-full min-h-0 flex-col border-t border-border bg-bg-elevated"
    >
      <header className="flex h-8 shrink-0 items-center gap-1 border-b border-border px-1 text-[12px]">
        {TABS.map((t) => {
          const active = t.id === tab;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => onTab(t.id)}
              aria-pressed={active}
              className={cn(
                "relative inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] uppercase tracking-wider transition-colors",
                active ? "text-fg" : "text-fg-muted hover:text-fg",
              )}
            >
              <t.Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
              {t.label}
              {active ? (
                <span
                  aria-hidden
                  className="absolute inset-x-1.5 -bottom-px h-px bg-accent"
                />
              ) : null}
            </button>
          );
        })}
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
        {tab === "terminal" ? (
          <TerminalPanel workspaceId={workspaceId} />
        ) : tab === "diff" ? (
          <DiffPanel workspaceId={workspaceId} />
        ) : null}
      </div>
    </section>
  );
}
