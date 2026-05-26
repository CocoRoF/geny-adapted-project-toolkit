import { Search } from "lucide-react";

import { EnvEditor } from "@/ide/EnvEditor";
import { FileTree } from "@/ide/FileTree";
import { GitPanel } from "@/ide/GitPanel";
import type { SideView } from "@/ide/shell/ActivityBar";
import { TestRunnerPanel } from "@/ide/TestRunnerPanel";

interface Props {
  view: SideView;
  workspaceId: string;
  onOpenFile: (path: string) => void;
}

const TITLES: Record<SideView, string> = {
  files: "Explorer",
  search: "Search",
  git: "Source Control",
  tests: "Tests",
  env: ".env Files",
};

/** The toggleable left panel. Title strip on top, view body fills
 * the rest. Width is controlled by the parent shell. */
export function SidePanel({ view, workspaceId, onOpenFile }: Props) {
  return (
    <aside
      data-view={view}
      className="flex h-full min-w-0 flex-col bg-bg-elevated"
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
          <GitPanel workspaceId={workspaceId} />
        ) : view === "tests" ? (
          <TestRunnerPanel workspaceId={workspaceId} />
        ) : view === "env" ? (
          <EnvEditor workspaceId={workspaceId} />
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
        Coming soon. For now, use the editor's <kbd className="rounded bg-bg px-1">Ctrl+F</kbd>
        {" "}or the terminal's <code>grep</code>.
      </p>
    </div>
  );
}
