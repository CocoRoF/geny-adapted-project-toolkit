import { FileText, GitBranch, Server, TerminalSquare } from "lucide-react";

import { cn } from "@/ui/cn";

interface Props {
  /** Phase N.5 — workspace name (was ``branch`` pre-N.5). Per-repo
   *  branches live on the GitPanel header now. */
  name: string;
  workspaceStatus: string;
  openFile: string | null;
  onToggleTerminal: () => void;
  /** Phase F — when false, the editor column is collapsed. Status
   *  bar shows an "Open editor" pill so the operator can bring it
   *  back without hunting through the activity bar. */
  editorOpen: boolean;
  onOpenEditor: () => void;
}

/** Bottom strip — VSCode-style status bar. Single 22px row. Left side
 * is workspace state (branch, sandbox), right side is per-action
 * quick toggles. Click handlers wired by the parent. */
export function StatusBar({
  name,
  workspaceStatus,
  openFile,
  onToggleTerminal,
  editorOpen,
  onOpenEditor,
}: Props) {
  const isRunning = workspaceStatus === "running";
  return (
    <footer
      role="contentinfo"
      className="flex h-[22px] shrink-0 items-center gap-3 border-t border-border bg-bg-elevated px-3 text-[11px] text-fg-muted"
    >
      <span className="inline-flex items-center gap-1.5">
        <GitBranch className="h-3 w-3" strokeWidth={1.5} />
        <span className="font-medium text-fg">{name || "—"}</span>
      </span>
      <span className="inline-flex items-center gap-1.5">
        <Server
          className={cn(
            "h-3 w-3",
            isRunning ? "text-success" : "text-fg-subtle",
          )}
          strokeWidth={1.5}
        />
        {workspaceStatus}
      </span>
      {openFile ? (
        <span className="ml-2 truncate text-fg-subtle" title={openFile}>
          {openFile}
        </span>
      ) : null}
      {!editorOpen ? (
        <button
          type="button"
          onClick={onOpenEditor}
          title="Open the editor column"
          className="ml-auto inline-flex items-center gap-1 rounded px-1.5 hover:bg-surface-hover hover:text-fg"
        >
          <FileText className="h-3 w-3" strokeWidth={1.5} />
          Open editor
        </button>
      ) : null}
      <button
        type="button"
        onClick={onToggleTerminal}
        title="Toggle Terminal (Ctrl+`)"
        className={cn(
          editorOpen ? "ml-auto" : "",
          "inline-flex items-center gap-1 rounded px-1.5 hover:bg-surface-hover hover:text-fg",
        )}
      >
        <TerminalSquare className="h-3 w-3" strokeWidth={1.5} />
        Terminal
      </button>
    </footer>
  );
}
