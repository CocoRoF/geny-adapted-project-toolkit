import { Search } from "lucide-react";

import { EnvEditor } from "@/ide/EnvEditor";
import { FileTree } from "@/ide/FileTree";
import { GitPanel } from "@/ide/GitPanel";
import { ServicesPanel } from "@/ide/ServicesPanel";
import type { SideView } from "@/ide/shell/ActivityBar";
import { TestRunnerPanel } from "@/ide/TestRunnerPanel";

interface Props {
  view: SideView;
  workspaceId: string;
  /** Phase N.4 — GitPanel needs the project id so it can list the
   *  project's repositories for the source-control repo selector. */
  projectId: string;
  onOpenFile: (path: string) => void;
  /** Phase F — Source Control's per-file row routes its click into
   *  the editor column's diff view (VSCode parity). */
  onOpenDiff: (path: string) => void;
  /** Phase N.3 — "Open in preview" from the Services panel adds a
   *  preview tab to the editor column. */
  onOpenPreview: (url: string, label: string) => void;
}

const TITLES: Record<SideView, string> = {
  files: "Explorer",
  search: "Search",
  git: "Source Control",
  tests: "Tests",
  env: ".env Files",
  services: "Services",
};

/** The toggleable left panel. Title strip on top, view body fills
 * the rest. Width is controlled by the parent shell. */
export function SidePanel({
  view,
  workspaceId,
  projectId,
  onOpenFile,
  onOpenDiff,
  onOpenPreview,
}: Props) {
  return (
    <aside
      data-view={view}
      // Phase N.3 — explicit right border so the seam against the
      // editor column is obvious (the SplitHandle's hover-only
      // accent was too subtle for the operator to find at rest).
      // `border-border-strong` matches the chat rail's left edge.
      className="flex h-full min-w-0 flex-col border-r border-border-strong bg-bg-elevated"
    >
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-border px-3 text-[11px] font-medium uppercase tracking-wider text-fg-muted">
        {TITLES[view]}
      </header>
      <div className="min-h-0 flex-1 overflow-hidden">
        {view === "files" ? (
          <FileTree workspaceId={workspaceId} onOpenFile={onOpenFile} />
        ) : view === "search" ? (
          <SearchPlaceholder />
        ) : view === "git" ? (
          <GitPanel workspaceId={workspaceId} projectId={projectId} onOpenDiff={onOpenDiff} />
        ) : view === "tests" ? (
          <TestRunnerPanel workspaceId={workspaceId} />
        ) : view === "env" ? (
          <EnvEditor workspaceId={workspaceId} onOpenFile={onOpenFile} />
        ) : view === "services" ? (
          <ServicesPanel workspaceId={workspaceId} onOpenPreview={onOpenPreview} />
        ) : null}
      </div>
    </aside>
  );
}

function SearchPlaceholder() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
      <Search className="h-6 w-6 text-fg-subtle" strokeWidth={1.5} />
      <p className="text-[12px] font-medium text-fg-muted">In-workspace search</p>
      <p className="text-[11px] text-fg-subtle">
        Coming soon. For now, use the editor's <kbd className="rounded bg-bg px-1">Ctrl+F</kbd> or
        the terminal's <code>grep</code>.
      </p>
    </div>
  );
}
