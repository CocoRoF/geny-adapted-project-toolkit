import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

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

type LoadState = "loading" | "ready" | "error";

const STATUS_KEY: Record<WorkspaceStatus, string> = {
  creating: "workspace.status.creating",
  running: "workspace.status.running",
  paused: "workspace.status.paused",
  stopped: "workspace.status.stopped",
  failed: "workspace.status.failed",
  archived: "workspace.status.archived",
};

/** `/projects/:pid` — project overview. Lists workspaces with their
 * status + start/stop affordances and links into the IDE shell
 * (Cycle 3.3b). "+ workspace" opens the create modal. */
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

  function onStop(workspaceId: string): void {
    void stopWorkspace(workspaceId).then(patchWorkspace).catch(reportError);
  }

  function onStart(workspaceId: string): void {
    void startWorkspace(workspaceId).then(patchWorkspace).catch(reportError);
  }

  function reportError(err: unknown): void {
    if (err instanceof ApiError) {
      setError(`${err.code}: ${err.reason}`);
    } else {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section className="project-detail">
      <Link to="/projects">{t("nav.back_to_projects")}</Link>
      <header className="project-detail-header">
        <h2>{project?.display_name ?? t("projects.title")}</h2>
        {project ? (
          <div className="project-detail-meta">
            <span>{project.slug}</span>
            <code>{project.git_remote_url}</code>
          </div>
        ) : null}
      </header>

      <section className="workspaces-section">
        <div className="workspaces-header">
          <h3>{t("workspaces.title")}</h3>
          <button type="button" onClick={() => setShowCreate(true)} disabled={state !== "ready"}>
            {t("workspaces.new")}
          </button>
        </div>

        {state === "loading" ? <p>{t("workspaces.loading")}</p> : null}
        {state === "error" ? (
          <p role="alert" className="workspaces-error">
            {error}
          </p>
        ) : null}

        {state === "ready" && workspaces.length === 0 ? <p>{t("workspaces.empty")}</p> : null}

        {state === "ready" && workspaces.length > 0 ? (
          <ul className="workspaces-list">
            {workspaces.map((w) => (
              <li key={w.id} className="workspace-row">
                <div className="workspace-row-main">
                  <Link to={`/projects/${projectId}/w/${w.id}`}>
                    <strong>{w.branch}</strong>
                  </Link>
                  <span className={`workspace-status workspace-status--${w.status}`}>
                    {t(STATUS_KEY[w.status] as Parameters<typeof t>[0])}
                  </span>
                </div>
                <div className="workspace-row-actions">
                  {w.status === "running" ? (
                    <button type="button" onClick={() => onStop(w.id)}>
                      {t("workspaces.actions.stop")}
                    </button>
                  ) : null}
                  {w.status === "stopped" || w.status === "paused" ? (
                    <button type="button" onClick={() => onStart(w.id)}>
                      {t("workspaces.actions.start")}
                    </button>
                  ) : null}
                  <Link to={`/projects/${projectId}/w/${w.id}`} className="workspace-open-link">
                    {t("workspaces.open")}
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {showCreate ? (
        <NewWorkspaceModal
          projectId={projectId}
          onClose={() => setShowCreate(false)}
          onCreated={(ws) => {
            setWorkspaces((prev) => [ws, ...prev]);
            setShowCreate(false);
          }}
        />
      ) : null}
    </section>
  );
}
