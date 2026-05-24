import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, ExternalLink, GitBranch, Pause, Play, Plus, Square } from "lucide-react";

import { ApiError } from "@/api/client";
import { type ProjectResponse, getProject } from "@/api/projects";
import {
  type WorkspaceResponse,
  type WorkspaceStatus,
  listWorkspaces,
  startWorkspace,
  stopWorkspace,
} from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";
import { NewWorkspaceModal } from "@/routes/NewWorkspaceModal";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";

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
            {workspaces.map((w) => (
              <li
                key={w.id}
                className="flex items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-surface-hover"
              >
                <div className="flex min-w-0 flex-1 items-center gap-3">
                  <GitBranch className="h-3.5 w-3.5 shrink-0 text-fg-muted" />
                  <Link
                    to={`/projects/${projectId}/w/${w.id}`}
                    className="truncate text-[13px] font-medium text-fg hover:text-accent"
                  >
                    {w.branch}
                  </Link>
                  <Badge tone={STATUS_TONE[w.status]}>
                    {t(STATUS_KEY[w.status] as Parameters<typeof t>[0])}
                  </Badge>
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
                  {w.status === "archived" || w.status === "failed" ? (
                    <Button variant="ghost" size="sm" disabled>
                      <Square className="h-3 w-3" />
                    </Button>
                  ) : null}
                  <Link
                    to={`/projects/${projectId}/w/${w.id}`}
                    className="inline-flex h-7 items-center gap-1 rounded-md bg-accent px-2.5 text-[12px] font-medium text-accent-fg hover:bg-accent/90"
                  >
                    {t("workspaces.open")}
                    <ExternalLink className="h-3 w-3" />
                  </Link>
                </div>
              </li>
            ))}
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
    </div>
  );
}
