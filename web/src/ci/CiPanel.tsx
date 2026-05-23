import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { type CiRun, type WorkflowRunStatus, listCiRuns } from "@/api/ci";
import { useI18n } from "@/app/providers/i18n-context";
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

/** Read-only list of recent CI workflow runs.
 *
 * Backed by `GET /api/projects/{pid}/ci/runs`. The endpoint surfaces
 * `ci.no_token` when the operator hasn't configured a GitHub token —
 * we render that as a help message instead of a generic error so
 * the operator knows the fix. */
export function CiPanel({ projectId }: Props) {
  const { t } = useI18n();
  const [branch, setBranch] = useState("");
  const [state, setState] = useState<LoadState>("loading");
  const [runs, setRuns] = useState<CiRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);

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

  return (
    <div className="ci-panel" data-panel-kind="ci">
      <header className="ci-panel-header">
        <h3>{t("ci.title")}</h3>
        <div className="ci-panel-filters">
          <label>
            <span>{t("ci.branch")}</span>
            <input
              type="text"
              value={branch}
              onChange={(e) => setBranch(e.currentTarget.value)}
              placeholder="main"
            />
          </label>
          <button type="button" onClick={refresh} disabled={state === "loading"}>
            {t("ci.refresh")}
          </button>
        </div>
      </header>

      {state === "loading" ? <p>{t("ci.loading")}</p> : null}
      {state === "error" ? (
        <p role="alert" className="ci-panel-error" data-error-code={errorCode ?? ""}>
          {error}
        </p>
      ) : null}

      {state === "ready" && runs.length === 0 ? <p>{t("ci.empty")}</p> : null}

      {state === "ready" && runs.length > 0 ? (
        <table className="ci-table" data-testid="ci-table">
          <thead>
            <tr>
              <th>{t("ci.col.name")}</th>
              <th>{t("ci.col.branch")}</th>
              <th>{t("ci.col.status")}</th>
              <th>{t("ci.col.sha")}</th>
              <th>{t("ci.col.link")}</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className={`ci-row ci-row--${run.status}`}>
                <td>{run.name}</td>
                <td>
                  <code>{run.head_branch}</code>
                </td>
                <td>{t(STATUS_KEY[run.status])}</td>
                <td>
                  <code>{run.head_sha.slice(0, 7)}</code>
                </td>
                <td>
                  <a href={run.html_url} target="_blank" rel="noopener noreferrer">
                    ↗
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
