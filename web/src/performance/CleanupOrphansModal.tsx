import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Trash2, X, XCircle } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type CleanupReport,
  type OrphanPlan,
  cleanupOrphans,
  previewOrphanCleanup,
} from "@/api/performance";
import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

interface Props {
  onClose: () => void;
  /** Called after a successful cleanup so the dashboard can
   * invalidate its cached state (the SSE stream refreshes on its
   * own ~2 s later, but a callback lets the caller close other
   * panels / clear local series rings). */
  onCleaned?: (report: CleanupReport) => void;
}

type Phase = "loading" | "ready" | "running" | "done" | "error";

/** Three-pane modal: preview (what'll happen) → confirm → result.
 *
 * The "delete worktrees" toggle is opt-in + warns explicitly:
 * removing host-side worktree dirs deletes any uncommitted user
 * work in them. Default is OFF; even running the safe cleanup
 * already wipes the container + Caddy routes which is what the
 * operator usually wanted. */
export function CleanupOrphansModal({ onClose, onCleaned }: Props) {
  const { t } = useI18n();
  const [phase, setPhase] = useState<Phase>("loading");
  const [plan, setPlan] = useState<OrphanPlan | null>(null);
  const [report, setReport] = useState<CleanupReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [removeWorktrees, setRemoveWorktrees] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const p = await previewOrphanCleanup();
        setPlan(p);
        setPhase("ready");
      } catch (e) {
        setErr(e instanceof ApiError ? e.reason : String(e));
        setPhase("error");
      }
    })();
  }, []);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape" && phase !== "running") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose, phase]);

  const run = async () => {
    setPhase("running");
    setErr(null);
    try {
      const r = await cleanupOrphans(removeWorktrees);
      setReport(r);
      setPhase("done");
      onCleaned?.(r);
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : String(e));
      setPhase("error");
    }
  };

  const nothing =
    plan &&
    plan.containers.length === 0 &&
    plan.caddy_route_ids.length === 0 &&
    plan.worktree_paths.length === 0 &&
    (plan.archived_projects?.length ?? 0) === 0;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget && phase !== "running") onClose();
      }}
    >
      <div className="flex max-h-[80vh] w-[min(720px,92vw)] flex-col overflow-hidden rounded-lg border border-border bg-bg-elevated shadow-xl">
        <header className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
          <Trash2 className="h-4 w-4 text-warn" strokeWidth={1.5} />
          <h2 className="text-[14px] font-semibold text-fg">
            {t("performance.cleanup.title")}
          </h2>
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            disabled={phase === "running"}
            className="ml-auto h-7 w-7 p-0"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {phase === "loading" ? (
            <div className="flex items-center gap-2 text-fg-muted">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-[12px]">{t("performance.cleanup.loading")}</span>
            </div>
          ) : null}

          {(phase === "ready" || phase === "running") && plan ? (
            <PlanView
              plan={plan}
              removeWorktrees={removeWorktrees}
              onToggleWorktrees={setRemoveWorktrees}
              running={phase === "running"}
              nothing={!!nothing}
            />
          ) : null}

          {phase === "done" && report ? <ReportView report={report} /> : null}

          {err ? (
            <p
              role="alert"
              className="mt-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
            >
              {err}
            </p>
          ) : null}
        </div>

        <footer className="flex shrink-0 items-center gap-2 border-t border-border bg-bg-subtle px-4 py-2.5">
          {phase === "done" ? (
            <Button variant="primary" onClick={onClose} className="ml-auto">
              {t("performance.cleanup.close")}
            </Button>
          ) : (
            <>
              <span className="text-[11px] text-fg-subtle">
                {t("performance.cleanup.escape_hint")}
              </span>
              <Button
                variant="ghost"
                onClick={onClose}
                disabled={phase === "running"}
                className="ml-auto"
              >
                {t("performance.cleanup.cancel")}
              </Button>
              <Button
                variant="danger"
                onClick={run}
                disabled={phase !== "ready" || !!nothing}
              >
                {phase === "running" ? (
                  <>
                    <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                    {t("performance.cleanup.running")}
                  </>
                ) : (
                  <>
                    <Trash2 className="mr-1 h-3.5 w-3.5" />
                    {t("performance.cleanup.confirm")}
                  </>
                )}
              </Button>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}

function PlanView({
  plan,
  removeWorktrees,
  onToggleWorktrees,
  running,
  nothing,
}: {
  plan: OrphanPlan;
  removeWorktrees: boolean;
  onToggleWorktrees: (v: boolean) => void;
  running: boolean;
  nothing: boolean;
}) {
  const { t } = useI18n();
  if (nothing) {
    return (
      <div className="flex flex-col items-center gap-2 py-8 text-center">
        <CheckCircle2 className="h-8 w-8 text-success" strokeWidth={1.5} />
        <p className="text-[13px] font-medium text-fg">{t("performance.cleanup.no_orphans")}</p>
        <p className="text-[11px] text-fg-subtle">{t("performance.cleanup.no_orphans_hint")}</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-[12px] text-fg-muted">{t("performance.cleanup.intro")}</p>

      {/* Containers */}
      <Section
        title={t("performance.cleanup.section.containers")}
        count={plan.containers.length}
      >
        {plan.containers.length === 0 ? (
          <Empty label={t("performance.cleanup.empty.containers")} />
        ) : (
          <ul className="divide-y divide-border/60">
            {plan.containers.map((c) => (
              <li key={c.container_id} className="flex items-center gap-2 py-1 text-[11.5px]">
                <code className="flex-1 truncate font-mono text-fg" title={c.container_name}>
                  {c.container_name}
                </code>
                <span className="rounded bg-bg-subtle px-1.5 py-px text-[10px] text-fg-muted">
                  {c.category}
                </span>
                <span
                  className={cn(
                    "rounded px-1.5 py-px text-[10px]",
                    c.status === "running"
                      ? "bg-success/15 text-success"
                      : "bg-bg-subtle text-fg-muted",
                  )}
                >
                  {c.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Caddy routes */}
      <Section
        title={t("performance.cleanup.section.caddy")}
        count={plan.caddy_route_ids.length}
      >
        {plan.caddy_route_ids.length === 0 ? (
          <Empty label={t("performance.cleanup.empty.caddy")} />
        ) : (
          <ul className="space-y-0.5">
            {plan.caddy_route_ids.map((id) => (
              <li key={id} className="truncate font-mono text-[11px] text-fg-muted">
                {id}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Archived projects — DB cascade purge */}
      <Section
        title={t("performance.cleanup.section.archived_projects")}
        count={plan.archived_projects?.length ?? 0}
      >
        {(plan.archived_projects?.length ?? 0) === 0 ? (
          <Empty label={t("performance.cleanup.empty.archived_projects")} />
        ) : (
          <>
            <p className="mb-1.5 text-[11.5px] text-fg-muted">
              {t("performance.cleanup.archived_projects.hint")}
            </p>
            <ul className="space-y-1">
              {plan.archived_projects.map((p) => (
                <li
                  key={p.project_id}
                  className="flex flex-wrap items-baseline gap-2 rounded border border-warn/40 bg-warn/5 px-2 py-1 text-[11.5px]"
                >
                  <span className="font-medium text-fg">{p.display_name}</span>
                  <code className="text-[10px] text-fg-subtle">
                    {p.project_id.slice(0, 12).toLowerCase()}…
                  </code>
                  <span className="text-[10.5px] text-fg-muted">
                    {t("performance.cleanup.archived_projects.cascade")
                      .replace("{ws}", String(p.cascade_workspaces))
                      .replace("{env}", String(p.cascade_environments))
                      .replace("{run}", String(p.cascade_deploy_runs))}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Section>

      {/* Worktrees — opt-in destructive */}
      <Section
        title={t("performance.cleanup.section.worktrees")}
        count={plan.worktree_paths.length}
      >
        {plan.worktree_paths.length === 0 ? (
          <Empty label={t("performance.cleanup.empty.worktrees")} />
        ) : (
          <>
            <label
              className={cn(
                "mb-1.5 flex cursor-pointer items-start gap-2 rounded border border-danger/40 bg-danger/5 px-2.5 py-2 text-[12px]",
                running && "pointer-events-none opacity-60",
              )}
            >
              <input
                type="checkbox"
                className="mt-0.5 h-3.5 w-3.5 accent-danger"
                checked={removeWorktrees}
                disabled={running}
                onChange={(e) => onToggleWorktrees(e.currentTarget.checked)}
              />
              <span>
                <span className="font-medium text-danger">
                  <AlertTriangle className="mr-1 inline h-3.5 w-3.5" strokeWidth={1.5} />
                  {t("performance.cleanup.worktrees.toggle")}
                </span>
                <span className="block text-[11px] text-fg-muted">
                  {t("performance.cleanup.worktrees.warning")}
                </span>
              </span>
            </label>
            <ul className="space-y-0.5">
              {plan.worktree_paths.map((p) => (
                <li
                  key={p}
                  className={cn(
                    "truncate font-mono text-[11px]",
                    removeWorktrees ? "text-danger" : "text-fg-muted line-through",
                  )}
                >
                  {p}
                </li>
              ))}
            </ul>
          </>
        )}
      </Section>
    </div>
  );
}

function ReportView({ report }: { report: CleanupReport }) {
  const { t } = useI18n();
  const okCount = report.containers.filter((c) => c.ok).length;
  const failCount = report.containers.length - okCount;
  return (
    <div className="space-y-3">
      <div className="rounded-md border border-success/40 bg-success/10 p-3">
        <h3 className="mb-1 text-[12px] font-semibold text-success">
          {t("performance.cleanup.report.done")}
        </h3>
        <ul className="text-[11.5px] text-success">
          <li>
            {t("performance.cleanup.report.containers")
              .replace("{ok}", String(okCount))
              .replace("{fail}", String(failCount))}
          </li>
          <li>
            {t("performance.cleanup.report.caddy").replace(
              "{n}",
              String(report.caddy_routes_removed.length),
            )}
          </li>
          <li>
            {t("performance.cleanup.report.worktrees").replace(
              "{n}",
              String(report.worktrees_removed.length),
            )}
          </li>
          <li>
            {t("performance.cleanup.report.projects_purged").replace(
              "{n}",
              String(report.projects_purged?.length ?? 0),
            )}
          </li>
        </ul>
      </div>
      {(report.project_purge_errors?.length ?? 0) > 0 ? (
        <div>
          <h3 className="mb-1 text-[12px] font-semibold text-warn">
            {t("performance.cleanup.report.project_purge_errors")}
          </h3>
          <ul className="space-y-0.5 text-[11.5px] text-warn">
            {report.project_purge_errors.map((e) => (
              <li key={e.project_id}>
                <span className="font-mono">{e.project_id}</span> — {e.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {failCount > 0 ? (
        <div>
          <h3 className="mb-1 text-[12px] font-semibold text-danger">
            {t("performance.cleanup.report.failures")}
          </h3>
          <ul className="space-y-0.5 text-[11.5px] text-danger">
            {report.containers
              .filter((c) => !c.ok)
              .map((c) => (
                <li key={c.container_id}>
                  <XCircle className="mr-1 inline h-3 w-3" />
                  <span className="font-mono">{c.container_name}</span>
                  <span className="text-fg-muted"> — {c.error}</span>
                </li>
              ))}
          </ul>
        </div>
      ) : null}
      {report.worktree_errors.length > 0 ? (
        <div>
          <h3 className="mb-1 text-[12px] font-semibold text-warn">
            {t("performance.cleanup.report.worktree_errors")}
          </h3>
          <ul className="space-y-0.5 text-[11.5px] text-warn">
            {report.worktree_errors.map((e) => (
              <li key={e.path}>
                <span className="font-mono">{e.path}</span> — {e.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-md border border-border bg-bg p-2.5">
      <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
        {title}
        <span className="rounded bg-bg-subtle px-1 py-0.5 text-[10px] tabular-nums text-fg">
          {count}
        </span>
      </h3>
      {children}
    </section>
  );
}

function Empty({ label }: { label: string }) {
  return <p className="text-[11px] text-fg-subtle">{label}</p>;
}
