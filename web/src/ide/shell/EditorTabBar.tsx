import { FileDiff, FileText, Globe, MoreHorizontal, X } from "lucide-react";
import { type KeyboardEvent, type MouseEvent, useEffect, useRef, useState } from "react";

import { useI18n } from "@/app/providers/i18n-context";
import { cn } from "@/ui/cn";

import type { EditorTab } from "./editor-tabs";

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
 * Overflow: the strip scrolls horizontally, but scroll alone hides
 * off-screen tabs with zero affordance — so a pinned `…` button on
 * the right (VSCode's "Open Editors" dropdown parity) lists EVERY
 * open tab vertically with activate + close actions. Activating from
 * the list also scrolls the chip into view. */
export function EditorTabBar({ tabs, activeTabId, onActivate, onClose }: Props) {
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuWrapRef = useRef<HTMLDivElement | null>(null);
  const stripRef = useRef<HTMLDivElement | null>(null);

  const close = (id: string, e: MouseEvent | KeyboardEvent) => {
    e.stopPropagation();
    onClose(id);
  };

  const activateAndReveal = (id: string) => {
    onActivate(id);
    // Bring the chip on-screen — the whole point of picking from the
    // list is that it was scrolled out of view.
    requestAnimationFrame(() => {
      stripRef.current
        ?.querySelector<HTMLElement>(`[data-tab-id="${CSS.escape(id)}"]`)
        ?.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    setMenuOpen(false);
  };

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: globalThis.MouseEvent) => {
      const wrap = menuWrapRef.current;
      if (wrap && e.target instanceof Node && !wrap.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  // Auto-reveal the active chip whenever activation happens from
  // anywhere (file tree, services panel…), not just from the menu.
  useEffect(() => {
    if (!activeTabId) return;
    stripRef.current
      ?.querySelector<HTMLElement>(`[data-tab-id="${CSS.escape(activeTabId)}"]`)
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeTabId]);

  return (
    <div className="flex h-8 shrink-0 items-stretch border-b border-border bg-bg-elevated">
      <div
        ref={stripRef}
        role="tablist"
        aria-label="open editor tabs"
        className="flex min-w-0 flex-1 items-stretch overflow-x-auto overflow-y-hidden"
      >
        {tabs.map((tab) => {
          const active = tab.id === activeTabId;
          const Icon = iconFor(tab);
          return (
            <button
              key={tab.id}
              data-tab-id={tab.id}
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

      {tabs.length > 0 ? (
        <div ref={menuWrapRef} className="relative flex shrink-0 items-stretch">
          <button
            type="button"
            aria-label={t("ide.tabs.list")}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title={t("ide.tabs.list")}
            onClick={() => setMenuOpen((v) => !v)}
            className={cn(
              "inline-flex items-center border-l border-border px-2 text-fg-muted",
              menuOpen ? "bg-bg-subtle text-fg" : "hover:bg-bg-subtle hover:text-fg",
            )}
          >
            <MoreHorizontal className="h-3.5 w-3.5" strokeWidth={1.5} />
          </button>
          {menuOpen ? (
            <div
              role="menu"
              aria-label={t("ide.tabs.list")}
              className="absolute right-0 top-full z-50 mt-1 max-h-[60vh] min-w-[240px] max-w-[360px] overflow-y-auto rounded-md border border-border bg-bg-elevated py-1 shadow-xl"
            >
              {tabs.map((tab) => {
                const active = tab.id === activeTabId;
                const Icon = iconFor(tab);
                return (
                  <div
                    key={tab.id}
                    role="menuitem"
                    className={cn(
                      "group flex w-full cursor-pointer items-center gap-2 px-2.5 py-1.5 text-[12px]",
                      active
                        ? "bg-accent/10 text-fg"
                        : "text-fg-muted hover:bg-bg-subtle hover:text-fg",
                    )}
                    onClick={() => activateAndReveal(tab.id)}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} />
                    <span className="min-w-0 flex-1 truncate font-mono" title={titleFor(tab)}>
                      {labelFor(tab)}
                    </span>
                    {tab.kind === "diff" ? (
                      <span className="rounded bg-accent/15 px-1 py-0.5 text-[9px] uppercase tracking-wider text-accent">
                        diff
                      </span>
                    ) : null}
                    {active ? (
                      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                    ) : null}
                    <button
                      type="button"
                      aria-label={`close ${labelFor(tab)}`}
                      onClick={(e) => close(tab.id, e)}
                      className="grid h-4 w-4 shrink-0 place-items-center rounded text-fg-subtle opacity-0 hover:bg-bg hover:text-fg group-hover:opacity-100"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}
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
