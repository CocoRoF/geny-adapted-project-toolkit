import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ExternalLink,
  History,
  Loader2,
  Plus,
  RotateCcw,
  Rocket,
  Undo2,
} from "lucide-react";

import {
  type DeployRunRow,
  type EnvironmentResponse,
  listDeployRuns,
  listEnvironments,
  streamDeploy,
  triggerRollback,
} from "@/api/environments";
import { useI18n } from "@/app/providers/i18n-context";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";

interface Props {
  projectId: string;
}

type StreamState = "idle" | "running" | "success" | "failed";

interface DeployLogState {
  envId: string;
  log: string;
  status: string;
  state: StreamState;
  resultUrl?: string | null;
}

const STATUS_TONE: Record<string, "neutral" | "success" | "warn" | "danger" | "accent"> = {
  success: "success",
  failed: "danger",
  running: "accent",
  pending: "neutral",
  rolled_back: "warn",
};

/** Workspace "Deploy" tab. Lists environments for this project and
 * lets the user trigger a live-streamed deploy. The dev preview
 * (Service tab) and the prod preview here use the same Caddy preview
 * stack — they differ only in HOW the upstream got there (live
 * bind-mount vs `docker compose up -d` artifact). */
export function DeployWorkspace({ projectId }: Props) {
  const { t } = useI18n();
  const [envs, setEnvs] = useState<EnvironmentResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [logs, setLogs] = useState<DeployLogState | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logScrollRef = useRef<HTMLPreElement | null>(null);
  // History UI state: which env is expanded, that env's recent
  // runs, plus a busy flag while a rollback is in flight. Hooks
  // that need `refresh` are declared further down after `refresh`.
  const [historyEnvId, setHistoryEnvId] = useState<string | null>(null);
  const [historyRuns, setHistoryRuns] = useState<DeployRunRow[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [rollbackBusyRunId, setRollbackBusyRunId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const rows = await listEnvironments(projectId);
      setEnvs(rows);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // Auto-scroll log pane to bottom when only a few lines from the end.
  useEffect(() => {
    const el = logScrollRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) {
      el.scrollTop = el.scrollHeight;
    }
  }, [logs?.log]);

  const startDeploy = useCallback(
    (env: EnvironmentResponse) => {
      abortRef.current?.abort();
      setLogs({ envId: env.id, log: "", status: "running", state: "running" });
      const ctrl = streamDeploy(
        env.id,
        // `two_factor_code` is required by the policy floor for
        // `deploy.prod` (and any env whose name matches). Solo
        // hobby deployments where the user clicking the deploy
        // button IS the consent → forward a non-empty token; the
        // dev-tier AcceptAnyCodeVerifier accepts any non-empty
        // string. Operators wanting real TOTP swap the verifier
        // dependency in `get_two_factor_verifier`.
        { version: "latest", target_options: {}, two_factor_code: "ui-click" },
        (frame) => {
          if (frame.type === "log" && frame.content) {
            setLogs((cur) =>
              cur && cur.envId === env.id
                ? { ...cur, log: cur.log + frame.content }
                : cur,
            );
          } else if (frame.type === "status" && frame.status) {
            setLogs((cur) =>
              cur && cur.envId === env.id ? { ...cur, status: frame.status! } : cur,
            );
          } else if (frame.type === "done" && frame.result) {
            const r = frame.result;
            setLogs((cur) => {
              if (!cur || cur.envId !== env.id) return cur;
              const nextState: StreamState = r.status === "success" ? "success" : "failed";
              const next: DeployLogState = {
                envId: cur.envId,
                log: cur.log + "\n" + (r.log || ""),
                status: r.status,
                state: nextState,
                resultUrl: r.bound_url ?? cur.resultUrl ?? null,
              };
              return next;
            });
            void refresh();
          }
        },
      );
      abortRef.current = ctrl;
    },
    [refresh],
  );

  const cancelDeploy = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLogs((cur) => (cur ? { ...cur, state: "idle" } : cur));
  }, []);

  const loadHistory = useCallback(async (envId: string) => {
    setHistoryLoading(true);
    try {
      const runs = await listDeployRuns(envId, 20);
      setHistoryRuns(runs);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const toggleHistory = useCallback(
    (envId: string) => {
      if (historyEnvId === envId) {
        setHistoryEnvId(null);
        setHistoryRuns([]);
        return;
      }
      setHistoryEnvId(envId);
      void loadHistory(envId);
    },
    [historyEnvId, loadHistory],
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
        await refresh();
        if (historyEnvId === envId) await loadHistory(envId);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setRollbackBusyRunId(null);
      }
    },
    [historyEnvId, loadHistory, refresh],
  );

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
              {envs.map((env) => (
                <li
                  key={env.id}
                  className="rounded-md border border-border bg-bg px-3 py-2 text-[12px]"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-fg">{env.name}</span>
                    <Badge tone="neutral">{env.deploy_target_kind}</Badge>
                  </div>
                  {env.last_run?.status ? (
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-fg-muted">
                      <Badge tone={STATUS_TONE[env.last_run.status] ?? "neutral"}>
                        {env.last_run.status}
                      </Badge>
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
                  ) : null}
                  {env.last_run?.bound_url ? (
                    <a
                      href={env.last_run.bound_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-1 inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                    >
                      <ExternalLink className="h-3 w-3" />
                      {env.last_run.bound_url}
                    </a>
                  ) : null}
                  <div className="mt-2 flex items-center gap-1">
                    <Button
                      variant="primary"
                      onClick={() => startDeploy(env)}
                      disabled={logs?.envId === env.id && logs.state === "running"}
                    >
                      {logs?.envId === env.id && logs.state === "running" ? (
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      ) : (
                        <Rocket className="mr-1 h-3 w-3" />
                      )}
                      {t("deploy.deploy")}
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => toggleHistory(env.id)}
                      title={t("deploy.history.toggle")}
                    >
                      <History className="mr-1 h-3 w-3" />
                      {t("deploy.history.toggle")}
                    </Button>
                    {logs?.envId === env.id && logs.state === "running" ? (
                      <Button variant="secondary" onClick={cancelDeploy}>
                        {t("deploy.cancel")}
                      </Button>
                    ) : null}
                  </div>
                  {historyEnvId === env.id ? (
                    <HistoryPane
                      runs={historyRuns}
                      loading={historyLoading}
                      busyRunId={rollbackBusyRunId}
                      onRollback={(run) => rollbackToRun(env.id, run)}
                    />
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
      <main className="flex h-full flex-col overflow-hidden bg-bg">
        <header className="flex shrink-0 items-center justify-between gap-2 border-b border-border bg-bg-elevated px-3 py-2">
          <span className="text-[12px] font-semibold text-fg">
            {logs
              ? `${t("deploy.log.for")} · ${envs.find((e) => e.id === logs.envId)?.name ?? "?"}`
              : t("deploy.log.idle")}
          </span>
          {logs?.state ? (
            <Badge tone={STATUS_TONE[logs.status] ?? "neutral"}>{logs.status}</Badge>
          ) : null}
          {logs?.resultUrl ? (
            <a
              href={logs.resultUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-1 text-[11px] font-medium text-fg hover:bg-surface-hover"
            >
              <ExternalLink className="h-3 w-3" />
              {t("deploy.open_url")}
            </a>
          ) : null}
          {logs?.state === "failed" ? (
            <Button variant="secondary" onClick={() => setLogs(null)}>
              <RotateCcw className="mr-1 h-3 w-3" />
              {t("deploy.dismiss")}
            </Button>
          ) : null}
        </header>
        <pre
          ref={logScrollRef}
          className="flex-1 overflow-auto whitespace-pre-wrap break-all bg-bg px-4 py-3 font-mono text-[11px] leading-relaxed text-fg-muted"
        >
          {logs?.log || t("deploy.log.placeholder")}
        </pre>
      </main>
    </div>
  );
}


function HistoryPane({
  runs,
  loading,
  busyRunId,
  onRollback,
}: {
  runs: DeployRunRow[];
  loading: boolean;
  busyRunId: string | null;
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
        return (
          <li
            key={r.id}
            className="flex items-start gap-1.5 rounded bg-bg-elevated px-2 py-1 text-[11px]"
          >
            <Badge tone={STATUS_TONE[r.status] ?? "neutral"}>{r.status}</Badge>
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
              {r.exec_code ? (
                <div className="text-warn">{r.exec_code}</div>
              ) : null}
            </div>
            <Button
              variant="ghost"
              onClick={() => onRollback(r)}
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

