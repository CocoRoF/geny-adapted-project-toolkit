import { FileDiff, FileText, Globe, X } from "lucide-react";
import { type KeyboardEvent, type MouseEvent } from "react";

import { cn } from "@/ui/cn";

import type { EditorTab } from "./IdeShell";

interface Props {
  tabs: EditorTab[];
  activeTabId: string | null;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
}

/** VSCode-style horizontal tab strip for the editor column. One
 * chip per open tab; icon reflects the tab kind. The active tab
 * gets an accent underline; the others fade to fg-muted. The X
 * on each chip closes that tab specifically; middle-click closes
 * too (matches VSCode + most browsers).
 *
 * The strip handles its own horizontal overflow with `overflow-x-auto`
 * — when many tabs are open the user scrolls within the strip rather
 * than the column itself wrapping. */
export function EditorTabBar({ tabs, activeTabId, onActivate, onClose }: Props) {
  const close = (id: string, e: MouseEvent | KeyboardEvent) => {
    e.stopPropagation();
    onClose(id);
  };
  return (
    <div
      role="tablist"
      aria-label="open editor tabs"
      className="flex h-8 shrink-0 items-stretch overflow-x-auto overflow-y-hidden border-b border-border bg-bg-elevated"
    >
      {tabs.map((tab) => {
        const active = tab.id === activeTabId;
        const Icon = iconFor(tab);
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={active}
            title={titleFor(tab)}
            onClick={() => onActivate(tab.id)}
            onAuxClick={(e) => {
              // Middle-click → close tab (VSCode/browser parity).
              if (e.button === 1) close(tab.id, e);
            }}
            className={cn(
              "group relative inline-flex shrink-0 items-center gap-1.5 border-r border-border px-2.5 text-[12px]",
              active
                ? "bg-bg text-fg"
                : "bg-bg-elevated text-fg-muted hover:bg-bg-subtle hover:text-fg",
            )}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
            <span className="max-w-[200px] truncate font-mono">{labelFor(tab)}</span>
            {tab.kind === "diff" ? (
              <span className="rounded bg-accent/15 px-1 py-0.5 text-[9px] uppercase tracking-wider text-accent">
                diff
              </span>
            ) : null}
            <span
              role="button"
              tabIndex={-1}
              aria-label={`close ${labelFor(tab)}`}
              onClick={(e) => close(tab.id, e)}
              className={cn(
                "ml-1 grid h-4 w-4 place-items-center rounded text-fg-subtle hover:bg-bg-subtle hover:text-fg",
                active ? "opacity-100" : "opacity-0 group-hover:opacity-100",
              )}
            >
              <X className="h-3 w-3" />
            </span>
            {active ? (
              <span
                aria-hidden
                className="absolute inset-x-1 bottom-0 h-[1.5px] rounded-full bg-accent"
              />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

function iconFor(tab: EditorTab) {
  if (tab.kind === "preview") return Globe;
  if (tab.kind === "diff") return FileDiff;
  return FileText;
}

function labelFor(tab: EditorTab): string {
  if (tab.kind === "preview") return tab.label;
  // file / diff — show basename, keep full path on title.
  const slash = tab.path.lastIndexOf("/");
  return slash >= 0 ? tab.path.slice(slash + 1) : tab.path;
}

function titleFor(tab: EditorTab): string {
  if (tab.kind === "preview") return tab.url;
  return tab.path;
}
