import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Archive, FolderGit2, GitBranch, Plus, RefreshCw, Trash2 } from "lucide-react";

import { ApiError } from "@/api/client";
import { type ProjectResponse, archiveProject, listProjects } from "@/api/projects";
import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";
import { NewProjectModal } from "@/routes/NewProjectModal";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { ConfirmDialog } from "@/ui/ConfirmDialog";

type LoadState = "idle" | "loading" | "ready" | "error";

/** `/projects` — card grid backed by `GET /api/projects`. */
export function ProjectsIndex() {
  const { t } = useI18n();
  const { me } = useAuth();
  const [state, setState] = useState<LoadState>("idle");
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState<ProjectResponse | null>(null);
  const [archiving, setArchiving] = useState(false);

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

  function onArchive(): void {
    if (!confirmArchive) return;
    const target = confirmArchive;
    setArchiving(true);
    void archiveProject(target.id)
      .then(() => {
        // Server returns the archived project (with archived_at set).
        // We drop it from the list — re-running listProjects() also
        // works but a quick local prune feels snappier.
        setProjects((prev) => prev.filter((p) => p.id !== target.id));
        setConfirmArchive(null);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof ApiError
            ? `${err.code}: ${err.reason}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      })
      .finally(() => setArchiving(false));
  }

  const orgs = me?.orgs ?? [];

  return (
    <div className="mx-auto max-w-[1080px] px-6 py-8">
      <header className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">
            {t("projects.title")}
          </h1>
          <p className="mt-0.5 text-[12px] text-fg-muted">
            {projects.length > 0
              ? t("projects.count").replace("{n}", String(projects.length))
              : t("projects.empty")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={refresh}
            disabled={state === "loading"}
            title={t("projects.refresh")}
          >
            <RefreshCw
              className={state === "loading" ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"}
            />
            <span>{t("projects.refresh")}</span>
          </Button>
          <Button
            variant="primary"
            onClick={() => setShowCreate(true)}
            disabled={orgs.length === 0}
          >
            <Plus className="h-3.5 w-3.5" />
            {t("projects.new")}
          </Button>
        </div>
      </header>

      {state === "loading" && projects.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border p-12 text-center text-[13px] text-fg-muted">
          {t("projects.loading")}
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

      {state === "ready" && projects.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-bg-elevated p-12 text-center">
          <FolderGit2 className="mx-auto mb-3 h-8 w-8 text-fg-subtle" />
          <p className="text-[13px] text-fg-muted">{t("projects.empty")}</p>
          <p className="mt-1 text-[12px] text-fg-subtle">
            Click "{t("projects.new")}" above to add one.
          </p>
        </div>
      ) : null}

      {projects.length > 0 ? (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <li key={p.id} className="group relative">
              <Link
                to={`/projects/${p.id}`}
                className="block h-full rounded-lg border border-border bg-bg-elevated p-4 transition-colors hover:border-accent/60 hover:bg-surface-hover"
              >
                <div className="mb-2 flex items-start justify-between gap-2">
                  <h3 className="truncate pr-8 text-[14px] font-semibold text-fg group-hover:text-accent">
                    {p.display_name}
                  </h3>
                  {p.archived_at ? (
                    <Badge tone="neutral">
                      <Archive className="mr-1 h-2.5 w-2.5" />
                      {t("projects.archived")}
                    </Badge>
                  ) : null}
                </div>
                <code className="block truncate text-[11px] text-fg-muted">{p.slug}</code>
                <div className="mt-3 flex items-center gap-1.5 text-[11px] text-fg-subtle">
                  <GitBranch className="h-3 w-3" />
                  <span className="truncate">{p.git_remote_url}</span>
                </div>
              </Link>
              {!p.archived_at ? (
                <button
                  type="button"
                  aria-label={t("projects.archive")}
                  title={t("projects.archive")}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setConfirmArchive(p);
                  }}
                  className="absolute right-3 top-3 grid h-7 w-7 place-items-center rounded-md text-fg-subtle opacity-0 transition-opacity hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      <NewProjectModal
        open={showCreate}
        orgs={orgs}
        onClose={() => setShowCreate(false)}
        onCreated={(project) => {
          setProjects((prev) => [project, ...prev]);
          setShowCreate(false);
        }}
      />

      <ConfirmDialog
        open={confirmArchive !== null}
        tone="danger"
        title={t("projects.archive_confirm.title")}
        description={t("projects.archive_confirm.body").replace(
          "{name}",
          confirmArchive?.display_name ?? "",
        )}
        confirmLabel={t("projects.archive_confirm.confirm")}
        cancelLabel={t("projects.archive_confirm.cancel")}
        busy={archiving}
        onConfirm={onArchive}
        onCancel={() => setConfirmArchive(null)}
      />
    </div>
  );
}
