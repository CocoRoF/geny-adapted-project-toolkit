import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  GitBranch,
  Loader2,
  Pause,
  Play,
  Plus,
  Trash2,
} from "lucide-react";

import { ApiError } from "@/api/client";
import { type ProjectResponse, getProject } from "@/api/projects";
import {
  type WorkspaceResponse,
  type WorkspaceStatus,
  deleteWorkspace,
  getWorkspaceCloneLog,
  listWorkspaces,
  startWorkspace,
  stopWorkspace,
} from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";
import { NewWorkspaceModal } from "@/routes/NewWorkspaceModal";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { ConfirmDialog } from "@/ui/ConfirmDialog";

type LoadState = "loading" | "ready" | "error";

const STATUS_KEY: Record<WorkspaceStatus, string> = {
  creating: "workspace.status.creating",
  running: "workspace.status.running",
  paused: "workspace.status.paused",
  stopped: "workspace.status.stopped",
  failed: "workspace.status.failed",
  archived: "workspace.status.archived",
};

const STATUS_TONE: Record<WorkspaceStatus, "neutral" | "accent" | "success" | "warn" | "danger"> = {
  creating: "neutral",
  running: "success",
  paused: "warn",
  stopped: "neutral",
  failed: "danger",
  archived: "neutral",
};

/** `/projects/:pid` — project overview + workspace list. */
export function ProjectDetail() {
  const { pid } = useParams();
  const { t } = useI18n();
  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceResponse[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<WorkspaceResponse | null>(null);
  const [deleting, setDeleting] = useState(false);

  const projectId = pid ?? "";

  const refresh = useCallback(() => {
    if (!projectId) return;
    setState("loading");
    Promise.all([getProject(projectId), listWorkspaces(projectId)])
      .then(([proj, wsList]) => {
        setProject(proj);
        setWorkspaces(wsList);
        setState("ready");
        setError(null);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setError(`${err.code}: ${err.reason}`);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
        setState("error");
      });
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll the workspace list while any row is in a transient state
  // (creating, paused). The server flips `creating → running` once
  // the background git clone settles, so the badge updates without
  // the user hitting refresh.
  const hasPending = workspaces.some((w) => w.status === "creating" || w.status === "paused");
  useEffect(() => {
    if (!hasPending || !projectId) return;
    const id = window.setInterval(() => {
      void listWorkspaces(projectId)
        .then(setWorkspaces)
        .catch(() => {
          /* swallow — keep last known state */
        });
    }, 2000);
    return () => window.clearInterval(id);
  }, [hasPending, projectId]);

  function patchWorkspace(updated: WorkspaceResponse): void {
    setWorkspaces((prev) => prev.map((w) => (w.id === updated.id ? updated : w)));
  }

  function reportError(err: unknown): void {
    if (err instanceof ApiError) setError(`${err.code}: ${err.reason}`);
    else setError(err instanceof Error ? err.message : String(err));
  }

  function onStop(workspaceId: string): void {
    void stopWorkspace(workspaceId).then(patchWorkspace).catch(reportError);
  }

  function onStart(workspaceId: string): void {
    void startWorkspace(workspaceId).then(patchWorkspace).catch(reportError);
  }

  function onDeleteConfirmed(): void {
    if (!confirmDelete) return;
    const target = confirmDelete;
    setDeleting(true);
    void deleteWorkspace(target.id)
      .then(() => {
        setWorkspaces((prev) => prev.filter((w) => w.id !== target.id));
        setConfirmDelete(null);
      })
      .catch(reportError)
      .finally(() => setDeleting(false));
  }

  return (
    <div className="mx-auto max-w-[1080px] px-6 py-8">
      <Link
        to="/projects"
        className="mb-3 inline-flex items-center gap-1 text-[12px] text-fg-muted hover:text-fg"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
        {t("nav.back_to_projects")}
      </Link>

      <header className="mb-6 flex flex-col gap-1 border-b border-border pb-5">
        <h1 className="text-[22px] font-semibold tracking-tight text-fg">
          {project?.display_name ?? t("projects.title")}
        </h1>
        {project ? (
          <div className="flex flex-wrap items-center gap-3 text-[12px]">
            <code className="rounded bg-bg-subtle px-1.5 py-0.5 text-fg-muted">{project.slug}</code>
            <a
              href={project.git_remote_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-fg-muted hover:text-accent"
            >
              <span className="max-w-[400px] truncate">{project.git_remote_url}</span>
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        ) : null}
      </header>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[15px] font-semibold text-fg">{t("workspaces.title")}</h2>
          <Button
            variant="primary"
            onClick={() => setShowCreate(true)}
            disabled={state !== "ready"}
          >
            <Plus className="h-3.5 w-3.5" />
            {t("workspaces.new")}
          </Button>
        </div>

        {state === "loading" ? (
          <div className="rounded-lg border border-dashed border-border p-8 text-center text-[12px] text-fg-muted">
            {t("workspaces.loading")}
          </div>
        ) : null}
        {state === "error" ? (
          <p
            role="alert"
            className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[13px] text-danger"
          >
            {error}
          </p>
        ) : null}

        {state === "ready" && workspaces.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border bg-bg-elevated p-8 text-center">
            <GitBranch className="mx-auto mb-2 h-6 w-6 text-fg-subtle" />
            <p className="text-[13px] text-fg-muted">{t("workspaces.empty")}</p>
          </div>
        ) : null}

        {workspaces.length > 0 ? (
          <ul className="overflow-hidden rounded-lg border border-border bg-bg-elevated divide-y divide-border">
            {workspaces.map((w) => {
              const isCreating = w.status === "creating";
              const isFailed = w.status === "failed";
              const showInlineLog = isCreating || isFailed;
              return (
                <li key={w.id} className="transition-colors hover:bg-surface-hover">
                  <div className="flex items-center justify-between gap-3 px-4 py-3">
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                      <GitBranch className="h-3.5 w-3.5 shrink-0 text-fg-muted" />
                      {isCreating ? (
                        <span className="truncate text-[13px] font-medium text-fg-muted">
                          {w.branch}
                        </span>
                      ) : (
                        <Link
                          to={`/projects/${projectId}/w/${w.id}`}
                          className="truncate text-[13px] font-medium text-fg hover:text-accent"
                        >
                          {w.branch}
                        </Link>
                      )}
                      <Badge tone={STATUS_TONE[w.status]}>
                        {isCreating ? (
                          <span className="inline-flex items-center gap-1">
                            <span className="relative inline-flex h-1.5 w-1.5">
                              <span className="absolute inset-0 animate-ping rounded-full bg-accent opacity-60" />
                              <span className="relative inline-block h-1.5 w-1.5 rounded-full bg-accent" />
                            </span>
                            {t(STATUS_KEY[w.status] as Parameters<typeof t>[0])}
                          </span>
                        ) : (
                          t(STATUS_KEY[w.status] as Parameters<typeof t>[0])
                        )}
                      </Badge>
                      {isCreating ? (
                        <span className="text-[11px] text-fg-subtle">
                          {t("workspaces.cloning_hint")}
                        </span>
                      ) : null}
                      {isFailed ? (
                        <span className="text-[11px] text-danger">
                          {t("workspaces.failed_hint")}
                        </span>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-1.5">
                      {w.status === "running" ? (
                        <Button variant="ghost" size="sm" onClick={() => onStop(w.id)}>
                          <Pause className="h-3 w-3" />
                          {t("workspaces.actions.stop")}
                        </Button>
                      ) : null}
                      {w.status === "stopped" || w.status === "paused" ? (
                        <Button variant="ghost" size="sm" onClick={() => onStart(w.id)}>
                          <Play className="h-3 w-3" />
                          {t("workspaces.actions.start")}
                        </Button>
                      ) : null}
                      {w.status !== "archived" ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setConfirmDelete(w)}
                          title={t("workspaces.actions.delete")}
                          aria-label={t("workspaces.actions.delete")}
                          className="text-fg-subtle hover:bg-danger/10 hover:text-danger"
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      ) : null}
                      {isCreating ? (
                        <span
                          aria-label={t("workspaces.cloning_hint")}
                          className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-bg-subtle px-2.5 text-[12px] font-medium text-fg-muted"
                        >
                          <Loader2 className="h-3 w-3 animate-spin" />
                          {t("workspaces.open")}
                        </span>
                      ) : (
                        <Link
                          to={`/projects/${projectId}/w/${w.id}`}
                          className="inline-flex h-7 items-center gap-1 rounded-md bg-accent px-2.5 text-[12px] font-medium text-accent-fg hover:bg-accent/90"
                        >
                          {t("workspaces.open")}
                          <ExternalLink className="h-3 w-3" />
                        </Link>
                      )}
                    </div>
                  </div>
                  {showInlineLog ? (
                    <InlineCloneLog workspaceId={w.id} live={isCreating} />
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>

      <NewWorkspaceModal
        open={showCreate}
        projectId={projectId}
        onClose={() => setShowCreate(false)}
        onCreated={(ws) => {
          setWorkspaces((prev) => [ws, ...prev]);
          setShowCreate(false);
        }}
      />

      <ConfirmDialog
        open={confirmDelete !== null}
        tone="danger"
        title={t("workspaces.delete_confirm.title")}
        description={t("workspaces.delete_confirm.body").replace(
          "{branch}",
          confirmDelete?.branch ?? "",
        )}
        confirmLabel={t("workspaces.delete_confirm.confirm")}
        cancelLabel={t("workspaces.delete_confirm.cancel")}
        busy={deleting}
        onConfirm={onDeleteConfirmed}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}

interface InlineCloneLogProps {
  workspaceId: string;
  /** `true` while status="creating" so we keep polling. */
  live: boolean;
}

/** Inline live `git clone` log shown directly under a workspace row
 * on the project detail page. The user shouldn't have to click "Open"
 * to find out what's happening — the log lives where the row lives.
 *
 * Polls every 2s while live; one-shot fetch when the workspace has
 * already settled (e.g. failed) so the user still sees what went
 * wrong. */
function InlineCloneLog({ workspaceId, live }: InlineCloneLogProps) {
  const { t } = useI18n();
  const [log, setLog] = useState<string>("");
  const [collapsed, setCollapsed] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let cancelled = false;
    const pull = () => {
      void getWorkspaceCloneLog(workspaceId, 8192).then((text) => {
        if (cancelled) return;
        setLog(text);
        const el = preRef.current;
        if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) {
          requestAnimationFrame(() => {
            el.scrollTop = el.scrollHeight;
          });
        }
      });
    };
    pull();
    if (!live) return undefined;
    const id = window.setInterval(pull, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [workspaceId, live]);

  const lines = log ? log.split("\n").filter(Boolean) : [];
  const lastLine = lines.length > 0 ? lines[lines.length - 1] : "";

  return (
    <div className="border-t border-border bg-bg-subtle/40">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-1.5 text-left text-[11px] font-mono text-fg-muted hover:bg-surface-hover"
      >
        {collapsed ? (
          <ChevronRight className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronDown className="h-3 w-3 shrink-0" />
        )}
        <span className="text-[10px] uppercase tracking-wide text-fg-subtle">
          clone.log
        </span>
        <span className="truncate flex-1 text-fg-muted">{lastLine || "—"}</span>
        <span className="shrink-0 text-fg-subtle">{lines.length} lines</span>
      </button>
      {!collapsed ? (
        <pre
          ref={preRef}
          data-testid="inline-clone-log"
          className="max-h-[260px] overflow-auto whitespace-pre-wrap break-all bg-bg px-4 py-2 font-mono text-[11px] leading-relaxed text-fg-muted"
        >
          {log || t("workspace.cloning.log_waiting")}
        </pre>
      ) : null}
    </div>
  );
}
