import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ExternalLink,
  History,
  Loader2,
  Plus,
  RotateCcw,
  Rocket,
  Settings,
  Square,
  Undo2,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type ActiveRun,
  type DeployRunRow,
  type EnvironmentResponse,
  cancelDeployRun,
  getActiveDeploy,
  listDeployRuns,
  listEnvironments,
  stopStack,
  triggerDeployAsync,
  triggerRollback,
} from "@/api/environments";
import { ConfirmDialog } from "@/ui/ConfirmDialog";
import { useI18n } from "@/app/providers/i18n-context";
import { EnvSettingsModal } from "@/ide/EnvSettingsModal";
import { RunDetailPanel } from "@/ide/RunDetailPanel";
import { useDeployStream } from "@/ide/useDeployStream";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

interface Props {
  projectId: string;
}

const STATUS_TONE: Record<string, "neutral" | "success" | "warn" | "danger" | "accent"> = {
  success: "success",
  failed: "danger",
  running: "accent",
  pending: "accent",
  aborted: "warn",
  rolled_back: "warn",
};

const TERMINAL_STATUSES = new Set(["success", "failed", "aborted", "rolled_back"]);

/** Workspace "Deploy" tab — persistent deploy management.
 *
 * Architecture:
 *   - The deploy itself runs server-side as a background task in
 *     `DeployRegistry`. The HTTP request that triggers it returns
 *     immediately with the `run_id`.
 *   - The UI subscribes to `/_gapt/api/deploy/runs/{run_id}/stream` via
 *     SSE. The server replays the captured log on connect, then
 *     live-tails new lines, then closes on `done`.
 *   - On mount, we poll `/_gapt/api/environments/{env_id}/deploy/active`
 *     for every env so a tab navigating back to the Deploy view
 *     sees the in-flight run already running — no state lost. */
export function DeployWorkspace({ projectId }: Props) {
  const { t } = useI18n();
  const [envs, setEnvs] = useState<EnvironmentResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // env_id → active run_id (null = no live run for this env).
  // Populated on mount via `/deploy/active` and after `triggerDeployAsync`.
  const [activeByEnv, setActiveByEnv] = useState<Record<string, ActiveRun | null>>({});
  // Which env's log is currently in the right pane.
  const [viewEnvId, setViewEnvId] = useState<string | null>(null);
  const viewRunId = viewEnvId ? activeByEnv[viewEnvId]?.run_id ?? null : null;
  const stream = useDeployStream(viewRunId);

  // Per-env tab selection. The env card switches between the
  // "deploy" pane (which shows the LIVE state or the in-progress
  // deploy — at most one thing at a time) and the "history" pane
  // (past runs only, excluding the live one). Settings is a modal,
  // not a tab content, but the button lives in the same tab strip
  // for visual symmetry.
  const [tabByEnv, setTabByEnv] = useState<Record<string, "deploy" | "history">>({});
  const tabFor = useCallback(
    (envId: string): "deploy" | "history" => tabByEnv[envId] ?? "deploy",
    [tabByEnv],
  );
  const setTab = useCallback(
    (envId: string, tab: "deploy" | "history") =>
      setTabByEnv((m) => ({ ...m, [envId]: tab })),
    [],
  );
  const [historyRuns, setHistoryRuns] = useState<DeployRunRow[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [rollbackBusyRunId, setRollbackBusyRunId] = useState<string | null>(null);
  // Selected past run for the right-pane detail view. When set, the
  // right pane shows `RunDetailPanel` instead of the live stream.
  // Cleared when the user clicks Deploy / View Logs (which want
  // live stream view) or opens a different env.
  const [detailRunId, setDetailRunId] = useState<string | null>(null);
  // Env whose settings modal is currently open. `null` = no modal.
  const [settingsEnv, setSettingsEnv] = useState<EnvironmentResponse | null>(
    null,
  );
  // Pending confirmations for destructive / disruptive stack ops.
  const [confirmStopEnvId, setConfirmStopEnvId] = useState<string | null>(null);
  const [confirmRedeployEnvId, setConfirmRedeployEnvId] = useState<string | null>(
    null,
  );
  const [stackBusyEnvId, setStackBusyEnvId] = useState<string | null>(null);

  const logScrollRef = useRef<HTMLPreElement | null>(null);

  const refreshEnvs = useCallback(async () => {
    try {
      const rows = await listEnvironments(projectId);
      setEnvs(rows);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refreshEnvs();
  }, [refreshEnvs]);

  // Whenever the envs list refreshes, poll `/deploy/active` for each
  // so we discover any deploy already in flight (e.g. user opened
  // this tab during an active deploy started elsewhere).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const entries = await Promise.all(
        envs.map(async (env) => {
          try {
            const run = await getActiveDeploy(env.id);
            return [env.id, run] as const;
          } catch {
            return [env.id, null] as const;
          }
        }),
      );
      if (cancelled) return;
      setActiveByEnv((cur) => {
        const next = { ...cur };
        for (const [eid, run] of entries) {
          next[eid] = run;
        }
        return next;
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [envs]);

  // When the stream lands a terminal event, drop the active mapping
  // for that env (server has already moved the run to terminal state)
  // and refresh envs so the `last_run` card updates.
  useEffect(() => {
    if (!viewEnvId) return;
    if (stream.phase !== "done") return;
    if (!TERMINAL_STATUSES.has(stream.status)) return;
    // Clear the env's active mapping; the run is over.
    setActiveByEnv((cur) => ({ ...cur, [viewEnvId]: null }));
    void refreshEnvs();
    if (tabFor(viewEnvId) === "history") {
      void loadHistory(viewEnvId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.phase, stream.status]);

  // Auto-scroll log pane to bottom when only a few lines from the end.
  useEffect(() => {
    const el = logScrollRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 100) {
      el.scrollTop = el.scrollHeight;
    }
  }, [stream.log]);

  // `loadHistoryRef` lets stopEnvStack call into loadHistory
  // without referencing the const before it's initialised. The
  // useCallback for stopEnvStack runs at line 186; loadHistory is
  // declared later (line 263). React deps would otherwise trip the
  // TDZ and blow up component init.
  const loadHistoryRef = useRef<((envId: string) => Promise<void>) | null>(null);
  const stopEnvStack = useCallback(
    async (envId: string) => {
      setStackBusyEnvId(envId);
      try {
        await stopStack(envId);
        await refreshEnvs();
        if (tabFor(envId) === "history" && loadHistoryRef.current) {
          void loadHistoryRef.current(envId);
        }
      } catch (e) {
        console.warn("stopStack failed", e);
      } finally {
        setStackBusyEnvId(null);
        setConfirmStopEnvId(null);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tabFor],
  );

  const startDeploy = useCallback(
    async (env: EnvironmentResponse) => {
      // Always switch the right pane to this env so the user sees
      // their click reflected. Also dismiss any historical detail
      // view so the live stream isn't hidden behind a past run.
      setDetailRunId(null);
      setViewEnvId(env.id);
      try {
        const accepted = await triggerDeployAsync(env.id, {
          version: "latest",
          target_options: {},
          two_factor_code: "ui-click",
        });
        setActiveByEnv((cur) => ({
          ...cur,
          [env.id]: {
            run_id: accepted.run_id,
            environment_id: accepted.environment_id,
            project_id: env.project_id,
            version: "latest",
            status: accepted.status,
            started_at: accepted.started_at,
            bound_url: null,
            exec_code: null,
            finished_at: null,
          },
        }));
      } catch (e) {
        if (e instanceof ApiError && e.code === "deploy.already_running") {
          // Another deploy is in flight for this env — refresh the
          // active map so we attach to it.
          const run = await getActiveDeploy(env.id).catch(() => null);
          if (run) {
            setActiveByEnv((cur) => ({ ...cur, [env.id]: run }));
            return;
          }
        }
        setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
      }
    },
    [],
  );

  const cancelCurrent = useCallback(async () => {
    if (!viewRunId) return;
    try {
      await cancelDeployRun(viewRunId);
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : String(e));
    }
  }, [viewRunId]);

  const loadHistory = useCallback(async (envId: string) => {
    setHistoryLoading(true);
    try {
      const runs = await listDeployRuns(envId, 20);
      setHistoryRuns(runs);
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setHistoryLoading(false);
    }
  }, []);
  // Expose loadHistory through the ref so stopEnvStack (declared
  // earlier in the file) can invoke it without forming a forward
  // reference at hook-init time.
  loadHistoryRef.current = loadHistory;

  const selectTab = useCallback(
    (envId: string, tab: "deploy" | "history") => {
      setTab(envId, tab);
      if (tab === "history") {
        void loadHistory(envId);
      }
    },
    [setTab, loadHistory],
  );

  const rollbackToRun = useCallback(
    async (envId: string, run: DeployRunRow) => {
      setRollbackBusyRunId(run.id);
      try {
        await triggerRollback(envId, {
          run_id: run.id,
          to_version: run.version,
          two_factor_code: "ui-click",
        });
        await refreshEnvs();
        if (tabFor(envId) === "history") await loadHistory(envId);
      } catch (e) {
        setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
      } finally {
        setRollbackBusyRunId(null);
      }
    },
    [tabFor, loadHistory, refreshEnvs],
  );

  const viewEnv = viewEnvId ? envs.find((e) => e.id === viewEnvId) : null;
  const viewActive = viewEnvId ? activeByEnv[viewEnvId] : null;
  const isRunning =
    !!viewActive && !TERMINAL_STATUSES.has(viewActive.status) && stream.phase !== "done";

  return (
    <div className="grid h-full grid-cols-[minmax(380px,_460px)_1fr] overflow-hidden">
      <aside className="flex h-full flex-col overflow-hidden border-r border-border bg-bg-elevated">
        <header className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2">
          <div className="flex items-center gap-1.5">
            <Rocket className="h-3.5 w-3.5 text-fg-muted" />
            <span className="text-[12px] font-semibold uppercase tracking-wide text-fg">
              {t("deploy.title")}
            </span>
          </div>
          <Link
            to={`/projects/${projectId}/environments`}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-fg-muted hover:bg-surface-hover hover:text-fg"
          >
            <Plus className="h-3 w-3" />
            {t("deploy.manage")}
          </Link>
        </header>
        <div className="flex-1 overflow-y-auto p-2">
          {loading ? (
            <p className="px-2 py-4 text-[12px] text-fg-subtle">{t("app.loading")}</p>
          ) : err ? (
            <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger">
              {err}
            </p>
          ) : envs.length === 0 ? (
            <div className="px-2 py-4">
              <p className="text-[12px] text-fg-muted">{t("deploy.empty")}</p>
              <Link
                to={`/projects/${projectId}/environments`}
                className="mt-2 inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
              >
                <Plus className="h-3 w-3" /> {t("deploy.create_first")}
              </Link>
            </div>
          ) : (
            <ul className="space-y-2">
              {envs.map((env) => {
                const active = activeByEnv[env.id];
                const envRunning = !!active && !TERMINAL_STATUSES.has(active.status);
                const tab = tabFor(env.id);
                // A run is "live" (and thus excluded from history) ONLY
                // when env.last_run is the success run currently
                // serving traffic. When stopped, the same run id is
                // still on env.last_run but the run belongs in
                // history — there's nothing live to surface in the
                // deploy tab anymore.
                const liveRunId =
                  env.last_run?.status === "success"
                    ? (env.last_run?.run_id ?? null)
                    : null;
                const pastRuns = liveRunId
                  ? historyRuns.filter((r) => r.id !== liveRunId)
                  : historyRuns;
                // run id that was just stopped, so HistoryPane can
                // render that entry with a "stopped" badge instead
                // of its original "success" status.
                const stoppedRunId =
                  env.last_run?.status === "stopped"
                    ? (env.last_run?.run_id ?? null)
                    : null;
                return (
                  <li
                    key={env.id}
                    className="rounded-md border border-border bg-bg px-3 py-2 text-[12px]"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-fg">{env.name}</span>
                      <Badge tone="neutral">{env.deploy_target_kind}</Badge>
                    </div>

                    {/* Tab strip — three buttons that look identical
                        but two are toggle ("deploy" / "history") and
                        the third opens the settings modal. */}
                    <div
                      role="tablist"
                      className="mt-2 flex items-center gap-1 rounded-md border border-border bg-bg-subtle p-0.5"
                    >
                      <button
                        type="button"
                        role="tab"
                        aria-selected={tab === "deploy"}
                        onClick={() => selectTab(env.id, "deploy")}
                        className={cn(
                          "flex flex-1 items-center justify-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors",
                          tab === "deploy"
                            ? "bg-accent/15 text-accent"
                            : "text-fg-muted hover:bg-bg hover:text-fg",
                        )}
                      >
                        <Rocket className="h-3 w-3" />
                        {t("deploy.tab.deploy")}
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={tab === "history"}
                        onClick={() => selectTab(env.id, "history")}
                        className={cn(
                          "flex flex-1 items-center justify-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors",
                          tab === "history"
                            ? "bg-accent/15 text-accent"
                            : "text-fg-muted hover:bg-bg hover:text-fg",
                        )}
                      >
                        <History className="h-3 w-3" />
                        {t("deploy.tab.history")}
                      </button>
                      <button
                        type="button"
                        onClick={() => setSettingsEnv(env)}
                        title={t("env_settings.button_title")}
                        className="flex flex-1 items-center justify-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-fg-muted transition-colors hover:bg-bg hover:text-fg"
                      >
                        <Settings className="h-3 w-3" />
                        {t("deploy.tab.settings")}
                      </button>
                    </div>
                    {/* ── DEPLOY tab — live state OR in-progress
                         deploy. Shows at most ONE card representing
                         "what's serving / what's deploying now". The
                         visual stops looking like a list of equivalent
                         success entries that were the old confusion
                         source. */}
                    {tab === "deploy" ? (
                      <div className="mt-2 space-y-2">
                        {envRunning ? (
                          // Active deploy: clickable card → opens live
                          // streaming pane on the right.
                          <button
                            type="button"
                            onClick={() => {
                              setViewEnvId(env.id);
                              setDetailRunId(null);
                            }}
                            className={cn(
                              "w-full rounded-md border border-accent/40 bg-accent/5 px-2 py-1.5 text-left transition-colors hover:bg-accent/10",
                              viewEnvId === env.id && "ring-1 ring-accent/40",
                            )}
                          >
                            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                              <Badge tone="accent">
                                <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
                                {active.status}
                              </Badge>
                              <span className="font-semibold uppercase tracking-wider text-accent">
                                {t("deploy.in_progress")}
                              </span>
                              <span className="font-mono text-fg-subtle">
                                run {active.run_id.slice(-6)}
                              </span>
                            </div>
                            <p className="mt-0.5 text-[11px] text-fg-muted">
                              {t("deploy.click_to_view_logs")}
                            </p>
                          </button>
                        ) : env.last_run?.status === "success" ? (
                          // LIVE — the only success card visible in
                          // this tab. Click → right pane opens the
                          // RunDetailPanel with live stack logs.
                          <button
                            type="button"
                            onClick={() => {
                              if (env.last_run?.run_id) {
                                setDetailRunId(env.last_run.run_id);
                                setViewEnvId(null);
                              }
                            }}
                            disabled={!env.last_run?.run_id}
                            className={cn(
                              "w-full rounded-md border border-success/40 bg-success/5 px-2 py-1.5 text-left transition-colors hover:bg-success/10",
                              env.last_run?.run_id && "cursor-pointer",
                              detailRunId === env.last_run?.run_id &&
                                "ring-1 ring-success/40",
                            )}
                          >
                            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                              <span className="inline-flex items-center gap-1 font-semibold uppercase tracking-wider text-success">
                                <span className="relative inline-flex h-2 w-2">
                                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success/60" />
                                  <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
                                </span>
                                {t("deploy.live")}
                              </span>
                              {env.last_run.version ? (
                                <span className="font-mono text-fg-subtle">
                                  {env.last_run.version}
                                </span>
                              ) : null}
                              {env.last_run.deployed_at ? (
                                <span className="font-mono text-fg-subtle">
                                  {new Date(env.last_run.deployed_at).toLocaleString()}
                                </span>
                              ) : null}
                            </div>
                            {env.last_run.bound_url ? (
                              <a
                                href={env.last_run.bound_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                onClick={(e) => e.stopPropagation()}
                                className="mt-0.5 inline-flex max-w-full items-center gap-1 truncate text-[11px] text-accent hover:underline"
                              >
                                <ExternalLink className="h-3 w-3 shrink-0" />
                                <span className="truncate">{env.last_run.bound_url}</span>
                              </a>
                            ) : null}
                            <p className="mt-0.5 text-[11px] text-fg-muted">
                              {t("deploy.click_to_view_logs")}
                            </p>
                          </button>
                        ) : env.last_run?.status === "failed" ? (
                          <button
                            type="button"
                            onClick={() => {
                              if (env.last_run?.run_id) {
                                setDetailRunId(env.last_run.run_id);
                                setViewEnvId(null);
                              }
                            }}
                            className={cn(
                              "w-full rounded-md border border-danger/40 bg-danger/5 px-2 py-1.5 text-left transition-colors hover:bg-danger/10",
                              detailRunId === env.last_run?.run_id &&
                                "ring-1 ring-danger/40",
                            )}
                          >
                            <Badge tone="danger">{env.last_run.status}</Badge>
                            <p className="mt-0.5 text-[11px] text-fg-muted">
                              {env.last_run.deployed_at
                                ? new Date(env.last_run.deployed_at).toLocaleString()
                                : ""}
                            </p>
                            <p className="mt-0.5 text-[11px] text-fg-muted">
                              {t("deploy.click_to_view_logs")}
                            </p>
                          </button>
                        ) : (
                          <p className="rounded-md border border-dashed border-border px-2 py-2 text-[11px] text-fg-subtle">
                            {stoppedRunId
                              ? t("deploy.idle_after_stop")
                              : t("deploy.idle_empty")}
                          </p>
                        )}
                        {/* Action row — varies by state so the
                            operator can't accidentally launch a
                            redeploy on top of a running stack. The
                            in-progress branch is the only one with
                            a single disabled button; the LIVE
                            branch forces a confirm before redeploy
                            AND surfaces "stop first" as the
                            primary path. */}
                        {envRunning ? (
                          <Button
                            variant="primary"
                            disabled
                            className="w-full"
                          >
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                            {t("deploy.in_progress")}
                          </Button>
                        ) : env.last_run?.status === "success" ? (
                          <div className="flex gap-1">
                            <Button
                              variant="danger"
                              onClick={() => setConfirmStopEnvId(env.id)}
                              disabled={stackBusyEnvId === env.id}
                              className="flex-1"
                            >
                              {stackBusyEnvId === env.id ? (
                                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                              ) : (
                                <Square className="mr-1 h-3 w-3" />
                              )}
                              {t("deploy.stop_stack")}
                            </Button>
                            <Button
                              variant="ghost"
                              onClick={() => setConfirmRedeployEnvId(env.id)}
                              disabled={stackBusyEnvId === env.id}
                              className="flex-1"
                              title={t("deploy.redeploy_hint")}
                            >
                              <RotateCcw className="mr-1 h-3 w-3" />
                              {t("deploy.redeploy")}
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="primary"
                            onClick={() => void startDeploy(env)}
                            className="w-full"
                          >
                            <Rocket className="mr-1 h-3 w-3" />
                            {env.last_run?.status === "failed"
                              ? t("deploy.redeploy")
                              : t("deploy.deploy")}
                          </Button>
                        )}
                      </div>
                    ) : null}

                    {/* ── HISTORY tab — past runs ONLY (live one
                         excluded). Click any → opens its detail in
                         the right pane. */}
                    {tab === "history" ? (
                      <HistoryPane
                        runs={pastRuns}
                        loading={historyLoading}
                        busyRunId={rollbackBusyRunId}
                        selectedRunId={detailRunId}
                        liveRunId={liveRunId}
                        stoppedRunId={stoppedRunId}
                        onView={(run) => {
                          setDetailRunId(run.id);
                          setViewEnvId(null);
                        }}
                        onRollback={(run) => rollbackToRun(env.id, run)}
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </aside>

      <main className="flex h-full flex-col overflow-hidden bg-bg">
        {detailRunId ? (
          // Past run detail view — DB-backed, immutable. Shows env
          // config + full log_tail + bound_url. Dismissable via the
          // header X button so the user can return to the live view.
          <div className="flex h-full flex-col">
            <div className="flex shrink-0 items-center gap-2 border-b border-border bg-bg-elevated/60 px-3 py-1.5">
              <span className="text-[11px] font-medium uppercase tracking-wider text-fg-muted">
                {t("deploy.detail.viewing")}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setDetailRunId(null)}
                className="ml-auto h-6 px-2 text-[11px]"
              >
                <RotateCcw className="mr-1 h-3 w-3" />
                {t("deploy.detail.back")}
              </Button>
            </div>
            <div className="min-h-0 flex-1">
              <RunDetailPanel runId={detailRunId} />
            </div>
          </div>
        ) : (
          <>
            <header className="flex shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-3 py-2">
              <span className="text-[12px] font-semibold text-fg">
                {viewEnv
                  ? `${t("deploy.log.for")} · ${viewEnv.name}`
                  : t("deploy.log.idle")}
              </span>
              {viewRunId ? (
                <Badge tone={STATUS_TONE[stream.status] ?? "neutral"}>
                  {stream.phase === "connecting"
                    ? t("deploy.connecting")
                    : stream.phase === "error"
                      ? t("deploy.reconnecting")
                      : stream.status}
                </Badge>
              ) : null}
              {viewRunId ? (
                <span className="font-mono text-[10.5px] text-fg-subtle">
                  run {viewRunId.slice(-12)}
                </span>
              ) : null}
              {stream.boundUrl ? (
                <a
                  href={stream.boundUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ml-auto inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-1 text-[11px] font-medium text-fg hover:bg-surface-hover"
                >
                  <ExternalLink className="h-3 w-3" />
                  {t("deploy.open_url")}
                </a>
              ) : null}
              {isRunning ? (
                <Button
                  variant="secondary"
                  onClick={() => void cancelCurrent()}
                  className={stream.boundUrl ? "" : "ml-auto"}
                >
                  {t("deploy.cancel")}
                </Button>
              ) : viewRunId && stream.phase === "done" ? (
                <Button
                  variant="ghost"
                  onClick={() => setViewEnvId(null)}
                  className={stream.boundUrl ? "" : "ml-auto"}
                >
                  <RotateCcw className="mr-1 h-3 w-3" />
                  {t("deploy.dismiss")}
                </Button>
              ) : null}
            </header>
            <pre
              ref={logScrollRef}
              className="flex-1 overflow-auto whitespace-pre-wrap break-all bg-bg px-4 py-3 font-mono text-[11px] leading-relaxed text-fg-muted"
            >
              {stream.log || t("deploy.log.placeholder")}
            </pre>
          </>
        )}
      </main>
      {settingsEnv ? (
        <EnvSettingsModal
          open
          env={settingsEnv}
          onClose={() => setSettingsEnv(null)}
          onSaved={(updated) => {
            setEnvs((prev) =>
              prev.map((e) => (e.id === updated.id ? updated : e)),
            );
            setSettingsEnv(updated);
          }}
        />
      ) : null}
      <ConfirmDialog
        open={confirmStopEnvId !== null}
        onCancel={() => setConfirmStopEnvId(null)}
        onConfirm={() => {
          if (confirmStopEnvId) void stopEnvStack(confirmStopEnvId);
        }}
        title={t("deploy.confirm_stop.title")}
        description={t("deploy.confirm_stop.body")}
        confirmLabel={t("deploy.stop_stack")}
        cancelLabel={t("deploy.confirm.cancel")}
        tone="danger"
        busy={stackBusyEnvId !== null}
      />
      <ConfirmDialog
        open={confirmRedeployEnvId !== null}
        onCancel={() => setConfirmRedeployEnvId(null)}
        onConfirm={() => {
          const id = confirmRedeployEnvId;
          setConfirmRedeployEnvId(null);
          const env = envs.find((e) => e.id === id);
          if (env) void startDeploy(env);
        }}
        title={t("deploy.confirm_redeploy.title")}
        description={t("deploy.confirm_redeploy.body")}
        confirmLabel={t("deploy.redeploy")}
        cancelLabel={t("deploy.confirm.cancel")}
        tone="neutral"
      />
    </div>
  );
}


function HistoryPane({
  runs,
  loading,
  busyRunId,
  selectedRunId,
  liveRunId,
  stoppedRunId,
  onView,
  onRollback,
}: {
  runs: DeployRunRow[];
  loading: boolean;
  busyRunId: string | null;
  selectedRunId: string | null;
  liveRunId: string | null;
  stoppedRunId: string | null;
  onView: (run: DeployRunRow) => void;
  onRollback: (run: DeployRunRow) => void;
}) {
  const { t } = useI18n();
  if (loading) {
    return (
      <p className="mt-2 flex items-center gap-1 text-[11px] text-fg-subtle">
        <Loader2 className="h-3 w-3 animate-spin" /> {t("app.loading")}
      </p>
    );
  }
  if (runs.length === 0) {
    return (
      <p className="mt-2 text-[11px] text-fg-subtle">
        {t("deploy.history.empty")}
      </p>
    );
  }
  return (
    <ul className="mt-2 space-y-1 border-t border-border/40 pt-2">
      {runs.map((r) => {
        const success = r.status === "success";
        const isSelected = selectedRunId === r.id;
        const isLive = liveRunId !== null && r.id === liveRunId;
        const isStopped = stoppedRunId !== null && r.id === stoppedRunId;
        return (
          <li
            key={r.id}
            className={cn(
              "flex items-start gap-1.5 rounded px-2 py-1 text-[11px]",
              "cursor-pointer transition-colors",
              isSelected
                ? "bg-accent/10 ring-1 ring-accent/40"
                : isLive
                  ? "bg-success/5 ring-1 ring-success/40"
                  : isStopped
                    ? "bg-fg-subtle/10 ring-1 ring-fg-subtle/30"
                    : "bg-bg-elevated hover:bg-surface-hover",
              !isLive && !isStopped && !isSelected && "opacity-80",
            )}
            onClick={() => onView(r)}
            title={t("deploy.history.click_to_view")}
          >
            {isLive ? (
              <span className="inline-flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-success">
                <span className="relative inline-flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success/60" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
                </span>
                {t("deploy.live")}
              </span>
            ) : isStopped ? (
              // The run whose stack was just stopped. Effective
              // status overrides the DeployRun.status="success"
              // value (the stop signal lives on env.last_run).
              <span className="inline-flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
                <span className="inline-flex h-1.5 w-1.5 rounded-full bg-fg-subtle" />
                {t("deploy.stopped")}
              </span>
            ) : (
              <Badge tone={STATUS_TONE[r.status] ?? "neutral"}>{r.status}</Badge>
            )}
            <div className="flex-1 overflow-hidden">
              <div className="font-mono text-fg" title={r.version}>
                {r.version.length > 24
                  ? `${r.version.slice(0, 8)}…${r.version.slice(-6)}`
                  : r.version}
              </div>
              <div className="text-fg-subtle">
                {r.trigger_kind} ·{" "}
                {r.finished_at
                  ? new Date(r.finished_at).toLocaleString()
                  : new Date(r.started_at).toLocaleString()}
              </div>
              {r.bound_url ? (
                <a
                  href={r.bound_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="inline-flex max-w-full items-center gap-1 truncate text-accent hover:underline"
                >
                  <ExternalLink className="h-2.5 w-2.5 shrink-0" />
                  <span className="truncate">{r.bound_url}</span>
                </a>
              ) : null}
              {r.exec_code ? (
                <div className="text-warn">{r.exec_code}</div>
              ) : null}
            </div>
            <Button
              variant="ghost"
              onClick={(e) => {
                e.stopPropagation();
                onRollback(r);
              }}
              disabled={!success || busyRunId === r.id}
              title={
                success ? t("deploy.history.rollback") : t("deploy.history.rollback_only_success")
              }
            >
              {busyRunId === r.id ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Undo2 className="h-3 w-3" />
              )}
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
