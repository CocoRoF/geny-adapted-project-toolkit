import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import { type ProjectResponse, listProjects } from "@/api/projects";
import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";
import { NewProjectModal } from "@/routes/NewProjectModal";

type LoadState = "idle" | "loading" | "ready" | "error";

/** `/projects` — card list backed by `GET /api/projects`, plus the
 * "+ project" affordance which opens the create modal. Real deep
 * features (compose auto-detect, GitHub Device Flow) ship in later
 * cycles once the backend exposes them. */
export function ProjectsIndex() {
  const { t } = useI18n();
  const { me } = useAuth();
  const [state, setState] = useState<LoadState>("idle");
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const refresh = useCallback(() => {
    setState("loading");
    listProjects()
      .then((rows) => {
        setProjects(rows);
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
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const orgs = me?.orgs ?? [];

  return (
    <section className="projects-index">
      <div className="projects-header">
        <h2>{t("projects.title")}</h2>
        <div className="projects-header-actions">
          <button type="button" onClick={refresh} disabled={state === "loading"}>
            {t("projects.refresh")}
          </button>
          <button type="button" onClick={() => setShowCreate(true)} disabled={orgs.length === 0}>
            {t("projects.new")}
          </button>
        </div>
      </div>

      {state === "loading" ? <p>{t("projects.loading")}</p> : null}
      {state === "error" ? (
        <p role="alert" className="projects-error">
          {error}
        </p>
      ) : null}
      {state === "ready" && projects.length === 0 ? <p>{t("projects.empty")}</p> : null}

      {state === "ready" && projects.length > 0 ? (
        <ul className="projects-list">
          {projects.map((p) => (
            <li key={p.id} className="project-card">
              <Link to={`/projects/${p.id}`}>
                <h3>{p.display_name}</h3>
                <div className="project-card-meta">
                  <span className="project-card-slug">{p.slug}</span>
                  <span className="project-card-org">
                    {t("projects.org")}: {p.org_id}
                  </span>
                </div>
                <code className="project-card-remote">{p.git_remote_url}</code>
                {p.archived_at ? (
                  <span className="project-card-archived">{t("projects.archived")}</span>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      {showCreate ? (
        <NewProjectModal
          orgs={orgs}
          onClose={() => setShowCreate(false)}
          onCreated={(project) => {
            setProjects((prev) => [project, ...prev]);
            setShowCreate(false);
          }}
        />
      ) : null}
    </section>
  );
}
