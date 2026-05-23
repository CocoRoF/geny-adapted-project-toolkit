import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { type AuditEntry, type AuditOutcome, listProjectAudit } from "@/api/audit";
import { useI18n } from "@/app/providers/i18n-context";

interface Props {
  projectId: string;
}

type LoadState = "loading" | "ready" | "error";

/** Read-only audit feed for a project.
 *
 * Backed by `GET /api/projects/{pid}/audit`. Filters: action prefix
 * (text input) + outcome (ok / error / any). Sorted ts descending.
 * Tail-poll style — manual refresh; M2 hooks Redis pub/sub so the
 * feed live-updates. */
export function AuditPanel({ projectId }: Props) {
  const { t, execMessage } = useI18n();
  const [actionPrefix, setActionPrefix] = useState("");
  const [outcome, setOutcome] = useState<AuditOutcome | "">("");
  const [state, setState] = useState<LoadState>("loading");
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setState("loading");
    const query: Parameters<typeof listProjectAudit>[1] = {};
    if (actionPrefix) query.action_prefix = actionPrefix;
    if (outcome) query.outcome = outcome;
    listProjectAudit(projectId, query)
      .then((rows) => {
        setEntries(rows);
        setState("ready");
        setError(null);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof ApiError
            ? `${err.code}: ${err.reason}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
        setState("error");
      });
  }, [projectId, actionPrefix, outcome]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="audit-panel" data-panel-kind="audit">
      <header className="audit-panel-header">
        <h3>{t("audit.title")}</h3>
        <div className="audit-panel-filters">
          <label>
            <span>{t("audit.filter.action")}</span>
            <input
              type="text"
              value={actionPrefix}
              onChange={(e) => setActionPrefix(e.currentTarget.value)}
              placeholder="agent."
            />
          </label>
          <label>
            <span>{t("audit.filter.outcome")}</span>
            <select
              value={outcome}
              onChange={(e) => setOutcome(e.currentTarget.value as AuditOutcome | "")}
            >
              <option value="">{t("audit.filter.outcome.any")}</option>
              <option value="ok">{t("audit.filter.outcome.ok")}</option>
              <option value="error">{t("audit.filter.outcome.error")}</option>
            </select>
          </label>
          <button type="button" onClick={refresh} disabled={state === "loading"}>
            {t("audit.refresh")}
          </button>
        </div>
      </header>

      {state === "loading" ? <p>{t("audit.loading")}</p> : null}
      {state === "error" ? (
        <p role="alert" className="audit-panel-error">
          {error}
        </p>
      ) : null}

      {state === "ready" && entries.length === 0 ? <p>{t("audit.empty")}</p> : null}

      {state === "ready" && entries.length > 0 ? (
        <table className="audit-table" data-testid="audit-table">
          <thead>
            <tr>
              <th>{t("audit.col.ts")}</th>
              <th>{t("audit.col.action")}</th>
              <th>{t("audit.col.actor")}</th>
              <th>{t("audit.col.outcome")}</th>
              <th>{t("audit.col.exec_code")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id} className={`audit-row audit-row--${entry.outcome}`}>
                <td>
                  <time dateTime={entry.ts}>{new Date(entry.ts).toLocaleString()}</time>
                </td>
                <td>
                  <code>{entry.action}</code>
                </td>
                <td>
                  <span>{entry.actor_type}</span>
                  {entry.actor_id ? <code className="audit-actor-id">{entry.actor_id}</code> : null}
                </td>
                <td>{entry.outcome}</td>
                <td>
                  {entry.exec_code ? (
                    <span title={execMessage(entry.exec_code)}>{entry.exec_code}</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
