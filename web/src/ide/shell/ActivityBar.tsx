import { type ReactNode } from "react";
import {
  FlaskConical,
  Files,
  GitBranch,
  Globe,
  KeyRound,
  type LucideIcon,
  MessageSquare,
  Search,
  Settings as SettingsIcon,
} from "lucide-react";

import { cn } from "@/ui/cn";

export type SideView =
  | "files"
  | "search"
  | "git"
  | "tests"
  | "env"
  | "services";

interface Item {
  id: SideView;
  icon: LucideIcon;
  label: string;
  shortcut?: string;
}

const ITEMS: Item[] = [
  { id: "files", icon: Files, label: "Explorer", shortcut: "Ctrl+Shift+E" },
  { id: "search", icon: Search, label: "Search", shortcut: "Ctrl+Shift+F" },
  { id: "git", icon: GitBranch, label: "Source Control", shortcut: "Ctrl+Shift+G" },
  { id: "tests", icon: FlaskConical, label: "Tests", shortcut: "Ctrl+Shift+T" },
  { id: "env", icon: KeyRound, label: ".env Files", shortcut: "Ctrl+Shift+V" },
  // Phase N.3 — "Services" launches dev servers + lets the user
  // open a Preview tab in the editor column (VSCode Simple Browser
  // parity). Replaces the old top-level "개발" tab.
  { id: "services", icon: Globe, label: "Services", shortcut: "Ctrl+Shift+S" },
];

interface Props {
  active: SideView | null;
  /** Clicking the active icon collapses (passes null). */
  onSelect: (v: SideView | null) => void;
  /** Whether the right-side Chat rail is open. Toggle via the bottom
   * Chat icon. */
  chatOpen: boolean;
  onToggleChat: () => void;
  /** Navigates to /settings. */
  onOpenSettings: () => void;
}

/** Vertical icon column — VSCode's Activity Bar. ~48px wide. The
 * top group selects which view the SidePanel shows; the bottom
 * group is for global toggles (Chat / Settings). */
export function ActivityBar({
  active,
  onSelect,
  chatOpen,
  onToggleChat,
  onOpenSettings,
}: Props) {
  return (
    <nav
      aria-label="activity bar"
      className="flex h-full w-12 shrink-0 flex-col items-center border-r border-border bg-bg-elevated py-1"
    >
      <div className="flex flex-col items-center gap-0.5">
        {ITEMS.map((it) => (
          <ActivityIcon
            key={it.id}
            active={active === it.id}
            label={`${it.label}${it.shortcut ? ` (${it.shortcut})` : ""}`}
            onClick={() => onSelect(active === it.id ? null : it.id)}
          >
            <it.icon className="h-5 w-5" strokeWidth={1.5} />
          </ActivityIcon>
        ))}
      </div>

      <div className="mt-auto flex flex-col items-center gap-0.5">
        <ActivityIcon
          active={chatOpen}
          label="Chat / Agent (Ctrl+Shift+A)"
          onClick={onToggleChat}
        >
          <MessageSquare className="h-5 w-5" strokeWidth={1.5} />
        </ActivityIcon>
        <ActivityIcon active={false} label="Settings" onClick={onOpenSettings}>
          <SettingsIcon className="h-5 w-5" strokeWidth={1.5} />
        </ActivityIcon>
      </div>
    </nav>
  );
}

function ActivityIcon({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "relative grid h-10 w-10 place-items-center rounded-md text-fg-muted transition-colors",
        "hover:text-fg",
        active && "text-fg",
      )}
    >
      {/* active indicator — 2px accent strip on the LEFT, matching VSCode */}
      {active ? (
        <span
          aria-hidden
          className="absolute left-0 top-1/2 h-7 w-0.5 -translate-y-1/2 rounded-r-full bg-accent"
        />
      ) : null}
      {children}
    </button>
  );
}
