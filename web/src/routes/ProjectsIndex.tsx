import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Archive,
  ChevronDown,
  Download,
  FolderGit2,
  FolderPlus,
  GitBranch,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";

import { ApiError } from "@/api/client";
import { type ProjectResponse, archiveProject, listProjects } from "@/api/projects";
import {
  type WorkspaceResponse,
  type WorkspaceStats,
  getWorkspaceStats,
  listAllActiveWorkspaces,
} from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";
import { ImportProjectModal } from "@/routes/ImportProjectModal";
import { NewProjectModal } from "@/routes/NewProjectModal";
import { NewProjectScaffoldModal } from "@/routes/NewProjectScaffoldModal";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { ConfirmDialog } from "@/ui/ConfirmDialog";

type LoadState = "idle" | "loading" | "ready" | "error";

/** `/projects` — card grid backed by `GET /api/projects`. */
export function ProjectsIndex() {
  const { t } = useI18n();
  const [state, setState] = useState<LoadState>("idle");
  const [projects, setProjects] = useState<ProjectResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false); // ← Import (legacy)
  // Phase N.2.6 — split "+ 새 프로젝트" into a dropdown menu with two
  // entry points: scaffold (create new repo) vs import (existing repo).
  // Phase N.4 adds a third: "empty" project (no git, multi-repo
  // designed via ProjectDetail's Repositories section).
  const [menuOpen, setMenuOpen] = useState(false);
  const [showScaffold, setShowScaffold] = useState(false);
  const [showEmpty, setShowEmpty] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Close the dropdown on outside click.
  useEffect(() => {
    if (!menuOpen) return;
    function handler(e: MouseEvent) {
      if (!menuRef.current) return;
      if (!menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);
  const [confirmArchive, setConfirmArchive] = useState<ProjectResponse | null>(null);
  const [archiving, setArchiving] = useState(false);
  // Phase C.2.d — show a warning banner when active workspaces are
  // approaching the configured cap. Stays silent below 80% and when
  // no cap is set.
  const [stats, setStats] = useState<WorkspaceStats | null>(null);
  // Phase C.2.a — all non-archived workspaces across all projects,
  // grouped on the client side for quick cross-project navigation.
  const [activeWorkspaces, setActiveWorkspaces] = useState<WorkspaceResponse[]>([]);

  useEffect(() => {
    let cancelled = false;
    void getWorkspaceStats()
      .then((s) => {
        if (!cancelled) setStats(s);
      })
      .catch(() => {
        // Stats are advisory — never block the projects page.
      });
    void listAllActiveWorkspaces()
      .then((rows) => {
        if (!cancelled) setActiveWorkspaces(rows);
      })
      .catch(() => {
        // Cross-project list is best-effort; the per-project list
        // inside ProjectDetail is still authoritative.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const workspacesByProject = useMemo(() => {
    const map = new Map<string, WorkspaceResponse[]>();
    for (const w of activeWorkspaces) {
      const arr = map.get(w.project_id) ?? [];
      arr.push(w);
      map.set(w.project_id, arr);
    }
    return map;
  }, [activeWorkspaces]);

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
          <div className="relative" ref={menuRef}>
            <Button
              variant="primary"
              onClick={() => setMenuOpen((v) => !v)}
              aria-expanded={menuOpen}
              aria-haspopup="menu"
            >
              <Plus className="h-3.5 w-3.5" />
              {t("projects.new")}
              <ChevronDown className="h-3 w-3 opacity-80" />
            </Button>
            {menuOpen ? (
              <ul
                role="menu"
                className="absolute right-0 top-full z-20 mt-1 w-56 overflow-hidden rounded-md border border-border bg-bg-elevated py-1 shadow-lg"
              >
                <li>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      setShowScaffold(true);
                    }}
                    className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-bg-subtle"
                  >
                    <Sparkles className="mt-0.5 h-3.5 w-3.5 text-accent" />
                    <div>
                      <div className="text-[13px] font-medium text-fg">새로 만들기</div>
                      <div className="text-[11px] text-fg-muted">
                        새 GitHub 레포 + 프리셋 스캐폴드
                      </div>
                    </div>
                  </button>
                </li>
                <li>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      setShowCreate(true);
                    }}
                    className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-bg-subtle"
                  >
                    <Download className="mt-0.5 h-3.5 w-3.5 text-fg-muted" />
                    <div>
                      <div className="text-[13px] font-medium text-fg">불러오기</div>
                      <div className="text-[11px] text-fg-muted">기존 git 레포 URL 등록</div>
                    </div>
                  </button>
                </li>
                {/* Phase N.4 — empty project entry. No git URL, no
                    preset. The Repositories section in ProjectDetail
                    is where the operator adds repos one at a time. */}
                <li>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      setShowEmpty(true);
                    }}
                    className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-bg-subtle"
                  >
                    <FolderPlus className="mt-0.5 h-3.5 w-3.5 text-fg-muted" />
                    <div>
                      <div className="text-[13px] font-medium text-fg">빈 프로젝트</div>
                      <div className="text-[11px] text-fg-muted">
                        git 없이 시작 + 나중에 레포 추가 (VS Code 식 multi-root)
                      </div>
                    </div>
                  </button>
                </li>
              </ul>
            ) : null}
          </div>
        </div>
      </header>

      {stats && stats.cap !== null && stats.active >= Math.ceil(stats.cap * 0.8) ? (
        <div
          role="status"
          className={
            stats.active >= stats.cap
              ? "mb-4 flex items-center gap-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
              : "mb-4 flex items-center gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-[12px] text-warn"
          }
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>
            {(stats.active >= stats.cap ? t("workspaces.cap.at_cap") : t("workspaces.cap.warning"))
              .replace("{active}", String(stats.active))
              .replace("{cap}", String(stats.cap))}
          </span>
        </div>
      ) : null}

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
          {projects.map((p) => {
            const wsForProject = workspacesByProject.get(p.id) ?? [];
            return (
              <li key={p.id} className="group relative">
                <div className="block h-full rounded-lg border border-border bg-bg-elevated transition-colors hover:border-accent/60">
                  <Link to={`/projects/${p.id}`} className="block p-4 hover:bg-surface-hover">
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
                    {/* Phase N.4 — render the multi-repo badge when the
                        project carries more than one repository; empty
                        projects (count 0) show "비어있음" so the operator
                        can tell at a glance which projects need a clone
                        before they can start. Single-repo (count 1)
                        keeps the legacy one-line view. */}
                    {p.repository_count > 1 ? (
                      <div className="mt-3 flex items-center gap-1.5 text-[11px] text-fg-subtle">
                        <GitBranch className="h-3 w-3" />
                        <span className="truncate">{p.repository_count}개 레포지토리</span>
                        <Badge tone="accent" className="text-[9px]">
                          multi
                        </Badge>
                      </div>
                    ) : p.repository_count === 0 ? (
                      <div className="mt-3 flex items-center gap-1.5 text-[11px] text-fg-subtle">
                        <GitBranch className="h-3 w-3" />
                        <span className="italic">비어있음 (레포 없음)</span>
                      </div>
                    ) : (
                      <div className="mt-3 flex items-center gap-1.5 text-[11px] text-fg-subtle">
                        <GitBranch className="h-3 w-3" />
                        <span className="truncate">{p.git_remote_url}</span>
                      </div>
                    )}
                  </Link>
                  {/* Phase C.2.a — show top 3 active workspaces inline
                      so the operator can jump straight into an IDE
                      without first navigating to the project detail. */}
                  {wsForProject.length > 0 ? (
                    <ul className="border-t border-border px-4 py-2 text-[11px]">
                      {wsForProject.slice(0, 3).map((w) => (
                        <li key={w.id} className="flex items-center gap-1.5 py-0.5">
                          <GitBranch className="h-3 w-3 shrink-0 text-fg-subtle" />
                          <Link
                            to={`/projects/${p.id}/w/${w.id}`}
                            className="truncate font-mono text-fg-muted hover:text-accent"
                            title={`${w.name} — ${w.status}`}
                          >
                            {w.name}
                          </Link>
                          <span
                            className={
                              w.status === "running"
                                ? "ml-auto inline-block h-1.5 w-1.5 rounded-full bg-success"
                                : w.status === "creating"
                                  ? "ml-auto inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent"
                                  : w.status === "failed"
                                    ? "ml-auto inline-block h-1.5 w-1.5 rounded-full bg-danger"
                                    : "ml-auto inline-block h-1.5 w-1.5 rounded-full bg-fg-subtle/40"
                            }
                            title={w.status}
                          />
                        </li>
                      ))}
                      {wsForProject.length > 3 ? (
                        <li className="pt-1 text-[10.5px] text-fg-subtle">
                          {t("workspaces.more").replace("{n}", String(wsForProject.length - 3))}
                        </li>
                      ) : null}
                    </ul>
                  ) : null}
                </div>
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
            );
          })}
        </ul>
      ) : null}

      <ImportProjectModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={(project) => {
          setProjects((prev) => [project, ...prev]);
          setShowCreate(false);
        }}
      />

      <NewProjectScaffoldModal
        open={showScaffold}
        onClose={() => setShowScaffold(false)}
        onCreated={(project) => {
          setProjects((prev) => [project, ...prev]);
          setShowScaffold(false);
        }}
      />

      {/* Phase N.4 — empty project. Same modal as the "불러오기"
          import flow but forced into emptyMode so the URL field
          hides and the submit POSTs with git_remote_url="". */}
      <NewProjectModal
        open={showEmpty}
        forceEmpty
        onClose={() => setShowEmpty(false)}
        onCreated={(project) => {
          setProjects((prev) => [project, ...prev]);
          setShowEmpty(false);
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
