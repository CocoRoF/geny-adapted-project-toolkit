import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import {
  type AuditEntry,
  type AuditOutcome,
  type AuditQuery,
  exportProjectAuditUrl,
  listProjectAudit,
} from "@/api/audit";
import { useI18n } from "@/app/providers/i18n-context";

interface Props {
  projectId: string;
}

type LoadState = "loading" | "ready" | "error";

type RangePreset = "today" | "7d" | "30d" | "all" | "custom";

const PAGE_SIZE = 100;

function isoForToday(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

function isoForDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

function resolveRange(
  preset: RangePreset,
  custom: { since: string; until: string },
): {
  since?: string;
  until?: string;
} {
  if (preset === "all") return {};
  if (preset === "today") return { since: isoForToday() };
  if (preset === "7d") return { since: isoForDaysAgo(7) };
  if (preset === "30d") return { since: isoForDaysAgo(30) };
  const out: { since?: string; until?: string } = {};
  if (custom.since) out.since = new Date(custom.since).toISOString();
  if (custom.until) out.until = new Date(custom.until).toISOString();
  return out;
}

/** Read-only audit feed for a project.
 *
 * Backed by `GET /api/projects/{pid}/audit` plus
 * `GET /api/projects/{pid}/audit/export` for CSV/JSONL downloads.
 * Filters: action prefix, outcome, time range (4 presets +
 * custom). "Load more" paginates by offset; the export endpoint
 * caps at 5000 rows server-side so large exports stay bounded. */
export function AuditPanel({ projectId }: Props) {
  const { t, execMessage } = useI18n();
  const [actionPrefix, setActionPrefix] = useState("");
  const [outcome, setOutcome] = useState<AuditOutcome | "">("");
  const [rangePreset, setRangePreset] = useState<RangePreset>("7d");
  const [customRange, setCustomRange] = useState({ since: "", until: "" });
  const [state, setState] = useState<LoadState>("loading");
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const baseQuery = useMemo<AuditQuery>(() => {
    const range = resolveRange(rangePreset, customRange);
    const q: AuditQuery = {};
    if (actionPrefix) q.action_prefix = actionPrefix;
    if (outcome) q.outcome = outcome;
    if (range.since) q.since = range.since;
    if (range.until) q.until = range.until;
    return q;
  }, [actionPrefix, outcome, rangePreset, customRange]);

  const refresh = useCallback(() => {
    setState("loading");
    listProjectAudit(projectId, { ...baseQuery, limit: PAGE_SIZE, offset: 0 })
      .then((rows) => {
        setEntries(rows);
        setHasMore(rows.length === PAGE_SIZE);
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
  }, [projectId, baseQuery]);

  const loadMore = useCallback(() => {
    if (state === "loading" || !hasMore) return;
    setState("loading");
    listProjectAudit(projectId, {
      ...baseQuery,
      limit: PAGE_SIZE,
      offset: entries.length,
    })
      .then((rows) => {
        setEntries((prev) => [...prev, ...rows]);
        setHasMore(rows.length === PAGE_SIZE);
        setState("ready");
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
  }, [projectId, baseQuery, entries.length, hasMore, state]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const csvUrl = exportProjectAuditUrl(projectId, "csv", baseQuery);
  const jsonlUrl = exportProjectAuditUrl(projectId, "jsonl", baseQuery);

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
          <label>
            <span>{t("audit.range.label")}</span>
            <select
              value={rangePreset}
              onChange={(e) => setRangePreset(e.currentTarget.value as RangePreset)}
            >
              <option value="today">{t("audit.range.today")}</option>
              <option value="7d">{t("audit.range.7d")}</option>
              <option value="30d">{t("audit.range.30d")}</option>
              <option value="all">{t("audit.range.all")}</option>
              <option value="custom">{t("audit.range.custom")}</option>
            </select>
          </label>
          {rangePreset === "custom" ? (
            <>
              <label>
                <span>{t("audit.filter.since")}</span>
                <input
                  type="datetime-local"
                  value={customRange.since}
                  onChange={(e) => setCustomRange({ ...customRange, since: e.currentTarget.value })}
                />
              </label>
              <label>
                <span>{t("audit.filter.until")}</span>
                <input
                  type="datetime-local"
                  value={customRange.until}
                  onChange={(e) => setCustomRange({ ...customRange, until: e.currentTarget.value })}
                />
              </label>
            </>
          ) : null}
          <button type="button" onClick={refresh} disabled={state === "loading"}>
            {t("audit.refresh")}
          </button>
          <a className="audit-panel-export" href={csvUrl} download data-testid="audit-export-csv">
            {t("audit.export.csv")}
          </a>
          <a
            className="audit-panel-export"
            href={jsonlUrl}
            download
            data-testid="audit-export-jsonl"
          >
            {t("audit.export.jsonl")}
          </a>
        </div>
      </header>

      {state === "loading" && entries.length === 0 ? <p>{t("audit.loading")}</p> : null}
      {state === "error" ? (
        <p role="alert" className="audit-panel-error">
          {error}
        </p>
      ) : null}

      {state === "ready" && entries.length === 0 ? <p>{t("audit.empty")}</p> : null}

      {entries.length > 0 ? (
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

      {hasMore ? (
        <div className="audit-panel-pagination">
          <button
            type="button"
            onClick={loadMore}
            disabled={state === "loading"}
            data-testid="audit-load-more"
          >
            {t("audit.load_more")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
