import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock,
  ExternalLink,
  Loader2,
  RefreshCw,
  RotateCw,
  XCircle,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type CiRun,
  type WorkflowRunStatus,
  fetchCiRunLogs,
  listCiRuns,
  rerunCiRun,
} from "@/api/ci";
import { useI18n } from "@/app/providers/i18n-context";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { Input } from "@/ui/Input";
import type { MessageKey } from "@/i18n";

interface Props {
  projectId: string;
}

type LoadState = "loading" | "ready" | "error";

const STATUS_KEY: Record<WorkflowRunStatus, MessageKey> = {
  queued: "ci.status.queued",
  in_progress: "ci.status.in_progress",
  completed_success: "ci.status.completed_success",
  completed_failure: "ci.status.completed_failure",
  completed_cancelled: "ci.status.completed_cancelled",
  completed_neutral: "ci.status.completed_neutral",
  unknown: "ci.status.unknown",
};

const STATUS_TONE: Record<WorkflowRunStatus, "neutral" | "success" | "warn" | "danger" | "accent"> =
  {
    queued: "neutral",
    in_progress: "accent",
    completed_success: "success",
    completed_failure: "danger",
    completed_cancelled: "neutral",
    completed_neutral: "neutral",
    unknown: "neutral",
  };

const STATUS_ICON: Record<WorkflowRunStatus, typeof CheckCircle2> = {
  queued: Clock,
  in_progress: CircleDot,
  completed_success: CheckCircle2,
  completed_failure: XCircle,
  completed_cancelled: XCircle,
  completed_neutral: CircleDot,
  unknown: CircleDot,
};

export function CiPanel({ projectId }: Props) {
  const { t } = useI18n();
  const [branch, setBranch] = useState("");
  const [state, setState] = useState<LoadState>("loading");
  const [runs, setRuns] = useState<CiRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);
  const [logs, setLogs] = useState<Record<number, { loading: boolean; text: string; truncated: boolean; err?: string | null }>>({});
  const [reruning, setRerunning] = useState<number | null>(null);

  const refresh = useCallback(() => {
    setState("loading");
    setError(null);
    setErrorCode(null);
    const opts: { branch?: string; limit?: number } = { limit: 20 };
    if (branch) opts.branch = branch;
    listCiRuns(projectId, opts)
      .then((rows) => {
        setRuns(rows);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) {
          setError(`${err.code}: ${err.reason}`);
          setErrorCode(err.code);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
        setState("error");
      });
  }, [projectId, branch]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const toggleLogs = useCallback(
    (runId: number) => {
      setExpandedRunId((cur) => (cur === runId ? null : runId));
      if (logs[runId]) return;
      setLogs((prev) => ({
        ...prev,
        [runId]: { loading: true, text: "", truncated: false },
      }));
      fetchCiRunLogs(projectId, runId)
        .then((resp) => {
          setLogs((prev) => ({
            ...prev,
            [runId]: { loading: false, text: resp.log, truncated: resp.truncated },
          }));
        })
        .catch((err: unknown) => {
          const msg =
            err instanceof ApiError ? err.reason : err instanceof Error ? err.message : String(err);
          setLogs((prev) => ({
            ...prev,
            [runId]: { loading: false, text: "", truncated: false, err: msg },
          }));
        });
    },
    [projectId, logs],
  );

  const onRerun = useCallback(
    async (runId: number, failedOnly: boolean) => {
      setRerunning(runId);
      try {
        await rerunCiRun(projectId, runId, { failed_only: failedOnly });
        // Give GitHub a beat to register the new run before we refresh.
        setTimeout(() => refresh(), 1500);
      } catch (err) {
        const msg =
          err instanceof ApiError ? err.reason : err instanceof Error ? err.message : String(err);
        setError(msg);
        setState("error");
      } finally {
        setRerunning(null);
      }
    },
    [projectId, refresh],
  );

  return (
    <div data-panel-kind="ci" className="flex h-full flex-col">
      <header className="flex items-end gap-3 border-b border-border bg-bg-elevated px-4 py-3">
        <div className="mr-auto">
          <h3 className="text-[14px] font-semibold text-fg">{t("ci.title")}</h3>
          <p className="text-[11px] text-fg-muted">{runs.length} runs</p>
        </div>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-fg-muted">
            {t("ci.branch")}
          </span>
          <Input
            value={branch}
            onChange={(e) => setBranch(e.currentTarget.value)}
            placeholder="main"
            className="h-7 w-[140px] text-[12px]"
          />
        </label>
        <Button variant="outline" onClick={refresh} disabled={state === "loading"}>
          <RefreshCw className={state === "loading" ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
          {t("ci.refresh")}
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto">
        {state === "loading" && runs.length === 0 ? (
          <p className="px-4 py-6 text-[12px] text-fg-muted">{t("ci.loading")}</p>
        ) : null}
        {state === "error" ? (
          <p
            role="alert"
            data-error-code={errorCode ?? ""}
            className="mx-4 my-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
          >
            {error}
          </p>
        ) : null}
        {state === "ready" && runs.length === 0 ? (
          <p className="px-4 py-6 text-center text-[12px] text-fg-muted">{t("ci.empty")}</p>
        ) : null}

        {runs.length > 0 ? (
          <table data-testid="ci-table" className="w-full table-auto text-[12px]">
            <thead className="sticky top-0 z-10 bg-bg-subtle text-left text-[10px] uppercase tracking-wide text-fg-muted">
              <tr>
                <th className="w-6 px-2 py-2" />
                <th className="px-4 py-2 font-medium">{t("ci.col.name")}</th>
                <th className="px-4 py-2 font-medium">{t("ci.col.branch")}</th>
                <th className="px-4 py-2 font-medium">{t("ci.col.status")}</th>
                <th className="px-4 py-2 font-medium">{t("ci.col.sha")}</th>
                <th className="px-4 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {runs.map((run) => {
                const Icon = STATUS_ICON[run.status];
                const expanded = expandedRunId === run.id;
                const log = logs[run.id];
                const isFailed =
                  run.status === "completed_failure" || run.status === "completed_cancelled";
                return (
                  <>
                    <tr
                      key={run.id}
                      className="cursor-pointer transition-colors hover:bg-surface-hover"
                      onClick={() => toggleLogs(run.id)}
                    >
                      <td className="px-2 py-1.5 text-fg-subtle">
                        {expanded ? (
                          <ChevronDown className="h-3.5 w-3.5" />
                        ) : (
                          <ChevronRight className="h-3.5 w-3.5" />
                        )}
                      </td>
                      <td className="max-w-[280px] truncate px-4 py-1.5 text-fg">{run.name}</td>
                      <td className="px-4 py-1.5">
                        <code className="rounded bg-bg-subtle px-1.5 py-0.5 text-[11px]">
                          {run.head_branch}
                        </code>
                      </td>
                      <td className="px-4 py-1.5">
                        <Badge tone={STATUS_TONE[run.status]}>
                          <Icon className="mr-1 h-3 w-3" />
                          {t(STATUS_KEY[run.status])}
                        </Badge>
                      </td>
                      <td className="px-4 py-1.5">
                        <code className="text-fg-muted">{run.head_sha.slice(0, 7)}</code>
                      </td>
                      <td
                        className="flex items-center gap-1 px-4 py-1.5"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onRerun(run.id, false)}
                          disabled={reruning === run.id}
                          className="h-6 px-1.5 text-[11px]"
                          title="Re-run all jobs"
                        >
                          {reruning === run.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <RotateCw className="h-3 w-3" />
                          )}
                          <span className="ml-1">Re-run</span>
                        </Button>
                        {isFailed ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onRerun(run.id, true)}
                            disabled={reruning === run.id}
                            className="h-6 px-1.5 text-[11px]"
                            title="Re-run failed jobs only"
                          >
                            failed
                          </Button>
                        ) : null}
                        <a
                          href={run.html_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-accent hover:bg-surface-hover"
                          title="Open on GitHub"
                        >
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </td>
                    </tr>
                    {expanded ? (
                      <tr>
                        <td colSpan={6} className="bg-bg p-0">
                          <div className="border-t border-border bg-bg-subtle/40">
                            {!log || log.loading ? (
                              <p className="flex items-center gap-1.5 px-4 py-3 text-[11px] text-fg-subtle">
                                <Loader2 className="h-3 w-3 animate-spin" /> Fetching logs…
                              </p>
                            ) : log.err ? (
                              <p
                                role="alert"
                                className="mx-4 my-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
                              >
                                {log.err}
                              </p>
                            ) : (
                              <>
                                {log.truncated ? (
                                  <p className="px-4 pt-2 text-[10px] text-warn">
                                    Log truncated. Open on GitHub for the full output.
                                  </p>
                                ) : null}
                                <pre className="m-0 max-h-[320px] overflow-auto px-4 py-2 font-mono text-[10.5px] leading-snug text-fg-muted">
                                  {log.text || "(empty log)"}
                                </pre>
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </>
                );
              })}
            </tbody>
          </table>
        ) : null}
      </div>
    </div>
  );
}
