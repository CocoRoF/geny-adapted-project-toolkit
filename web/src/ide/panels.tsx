import { type IDockviewPanelProps } from "dockview";
import { useEffect, useState } from "react";

import { useI18n } from "@/app/providers/i18n-context";
import { AuditPanel } from "@/audit/AuditPanel";
import { ChatPanel } from "@/chat/ChatPanel";
import { CiPanel } from "@/ci/CiPanel";
import { CostPanel } from "@/cost/CostPanel";
import { DiffPanel } from "@/ide/DiffPanel";
import { FileEditor } from "@/ide/Editor";
import { useEditorBus } from "@/ide/editor-store";
import { EnvEditor } from "@/ide/EnvEditor";
import { FileTree } from "@/ide/FileTree";
import { GitPanel } from "@/ide/GitPanel";
import { PreviewPanel } from "@/ide/PreviewPanel";
import { ServicesPanel } from "@/ide/ServicesPanel";
import { TerminalPanel } from "@/ide/TerminalPanel";
import { TestRunnerPanel } from "@/ide/TestRunnerPanel";

/** Placeholder panel — used by every leaf that hasn't shipped yet. */
export function PanelPlaceholder(props: IDockviewPanelProps<{ kind: string }>) {
  const { t } = useI18n();
  const kind = props.params.kind;
  return (
    <div
      data-panel-kind={kind}
      className="grid h-full place-items-center bg-bg-elevated text-fg-muted"
    >
      <div className="text-center">
        <p className="text-[12px] font-medium uppercase tracking-wider text-fg-subtle">{kind}</p>
        <p className="mt-1 text-[12px]">{t("ide.placeholder")}</p>
      </div>
    </div>
  );
}

/** File tree panel — bridges the tree's `onOpenFile` callback into
 * the EditorBus so the editor panel (which lives under a separate
 * dockview root) picks up the request. */
export function FileTreePanel(props: IDockviewPanelProps<{ workspaceId: string }>) {
  const bus = useEditorBus();
  return (
    <div data-panel-kind="tree" className="h-full overflow-y-auto bg-bg-elevated">
      <FileTree workspaceId={props.params.workspaceId} onOpenFile={(path) => bus.emit(path)} />
    </div>
  );
}

/** CI panel — read-only list of recent GitHub Actions runs. */
export function CiPanelDock(props: IDockviewPanelProps<{ projectId: string }>) {
  return (
    <div data-panel-kind="ci" className="h-full bg-bg-elevated">
      <CiPanel projectId={props.params.projectId} />
    </div>
  );
}

/** Cost panel — org-wide cost dashboard (totals, per-project, daily). */
export function CostPanelDock(_props: IDockviewPanelProps<Record<string, never>>) {
  return (
    <div data-panel-kind="cost" className="h-full bg-bg-elevated">
      <CostPanel />
    </div>
  );
}

/** Audit panel — read-only view of the project audit feed. */
export function AuditPanelDock(props: IDockviewPanelProps<{ projectId: string }>) {
  return (
    <div data-panel-kind="audit" className="h-full bg-bg-elevated">
      <AuditPanel projectId={props.params.projectId} />
    </div>
  );
}

/** Preview panel — wraps `<PreviewPanel>` with the dockview panel
 * contract. Persists the URL per workspace in LocalStorage. */
export function PreviewPanelDock(props: IDockviewPanelProps<{ workspaceId: string }>) {
  return (
    <div data-panel-kind="preview" className="h-full bg-bg-elevated">
      <PreviewPanel workspaceId={props.params.workspaceId} />
    </div>
  );
}

/** Chat panel — wraps `<ChatPanel>` with the dockview panel contract. */
export function ChatPanelDock(
  props: IDockviewPanelProps<{ workspaceId: string; projectId: string }>,
) {
  return (
    <div data-panel-kind="chat" className="h-full bg-bg-elevated">
      <ChatPanel projectId={props.params.projectId} workspaceId={props.params.workspaceId} />
    </div>
  );
}

/** Terminal panel — bidirectional PTY shell into the workspace
 * worktree (mock backend: host PTY rooted in the worktree dir). */
export function TerminalPanelDock(props: IDockviewPanelProps<{ workspaceId: string }>) {
  return (
    <div data-panel-kind="terminal" className="h-full bg-bg">
      <TerminalPanel workspaceId={props.params.workspaceId} />
    </div>
  );
}

/** Services panel — start/stop/expose long-running dev servers
 * inside the worktree. */
export function ServicesPanelDock(props: IDockviewPanelProps<{ workspaceId: string }>) {
  return (
    <div data-panel-kind="services" className="h-full bg-bg">
      <ServicesPanel workspaceId={props.params.workspaceId} />
    </div>
  );
}

/** Diff panel — shows the working-tree-vs-HEAD diff for the workspace. */
export function DiffPanelDock(props: IDockviewPanelProps<{ workspaceId: string }>) {
  return (
    <div data-panel-kind="diff" className="h-full bg-bg-elevated">
      <DiffPanel workspaceId={props.params.workspaceId} />
    </div>
  );
}

/** .env editor — surfaces every env file the introspector found,
 * one-click pick + edit + save. Routes through the workspace files
 * API (same path the agent uses) so writes are visible to both
 * sides. */
export function EnvPanelDock(props: IDockviewPanelProps<{ workspaceId: string }>) {
  return (
    <div data-panel-kind="env" className="h-full bg-bg-elevated">
      <EnvEditor workspaceId={props.params.workspaceId} />
    </div>
  );
}

/** Test runner — SSE-streamed `<test_command>` output. The default
 * command comes from introspection (`scripts.test`, `pytest` etc.). */
export function TestsPanelDock(props: IDockviewPanelProps<{ workspaceId: string }>) {
  return (
    <div data-panel-kind="tests" className="h-full bg-bg-elevated">
      <TestRunnerPanel workspaceId={props.params.workspaceId} />
    </div>
  );
}

/** Source-control panel — git status + commit + push + PR. All
 * git operations run inside the workspace sandbox. */
export function GitPanelDock(props: IDockviewPanelProps<{ workspaceId: string }>) {
  return (
    <div data-panel-kind="git" className="h-full bg-bg-elevated">
      <GitPanel workspaceId={props.params.workspaceId} />
    </div>
  );
}


/** Editor panel — subscribes to EditorBus so the tree can hand it
 * the path to open. */
export function EditorPanel(props: IDockviewPanelProps<{ workspaceId: string }>) {
  const bus = useEditorBus();
  const [openPath, setOpenPath] = useState<string | null>(null);

  useEffect(() => {
    return bus.subscribe((path) => setOpenPath(path));
  }, [bus]);

  return (
    <div data-panel-kind="editor" className="h-full bg-bg">
      <FileEditor workspaceId={props.params.workspaceId} openPath={openPath} />
    </div>
  );
}
