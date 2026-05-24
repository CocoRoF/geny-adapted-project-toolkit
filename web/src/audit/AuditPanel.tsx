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
    <div data-panel-kind="audit" className="flex h-full flex-col">
      <header className="flex flex-wrap items-end gap-2 border-b border-border bg-bg-elevated px-4 py-3">
        <div className="mr-auto">
          <h3 className="text-[14px] font-semibold text-fg">{t("audit.title")}</h3>
          <p className="text-[11px] text-fg-muted">{entries.length} entries</p>
        </div>

        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-fg-muted">
            {t("audit.filter.action")}
          </span>
          <input
            type="text"
            value={actionPrefix}
            onChange={(e) => setActionPrefix(e.currentTarget.value)}
            placeholder="agent."
            className="h-7 w-[140px] rounded-md border border-border bg-surface px-2 text-[12px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-fg-muted">
            {t("audit.filter.outcome")}
          </span>
          <select
            value={outcome}
            onChange={(e) => setOutcome(e.currentTarget.value as AuditOutcome | "")}
            className="h-7 w-[110px] rounded-md border border-border bg-surface px-2 text-[12px] text-fg focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="">{t("audit.filter.outcome.any")}</option>
            <option value="ok">{t("audit.filter.outcome.ok")}</option>
            <option value="error">{t("audit.filter.outcome.error")}</option>
          </select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-fg-muted">
            {t("audit.range.label")}
          </span>
          <select
            value={rangePreset}
            onChange={(e) => setRangePreset(e.currentTarget.value as RangePreset)}
            className="h-7 w-[120px] rounded-md border border-border bg-surface px-2 text-[12px] text-fg focus:outline-none focus:ring-2 focus:ring-accent"
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
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-fg-muted">
                {t("audit.filter.since")}
              </span>
              <input
                type="datetime-local"
                value={customRange.since}
                onChange={(e) => setCustomRange({ ...customRange, since: e.currentTarget.value })}
                className="h-7 rounded-md border border-border bg-surface px-2 text-[12px] text-fg focus:outline-none focus:ring-2 focus:ring-accent"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-fg-muted">
                {t("audit.filter.until")}
              </span>
              <input
                type="datetime-local"
                value={customRange.until}
                onChange={(e) => setCustomRange({ ...customRange, until: e.currentTarget.value })}
                className="h-7 rounded-md border border-border bg-surface px-2 text-[12px] text-fg focus:outline-none focus:ring-2 focus:ring-accent"
              />
            </label>
          </>
        ) : null}

        <div className="flex items-end gap-1.5">
          <button
            type="button"
            onClick={refresh}
            disabled={state === "loading"}
            className="h-7 rounded-md border border-border bg-surface px-2.5 text-[12px] font-medium text-fg hover:bg-surface-hover disabled:opacity-50"
          >
            {t("audit.refresh")}
          </button>
          <a
            href={csvUrl}
            download
            data-testid="audit-export-csv"
            className="h-7 rounded-md border border-border bg-surface px-2.5 text-[12px] font-medium text-fg hover:bg-surface-hover inline-flex items-center"
          >
            {t("audit.export.csv")}
          </a>
          <a
            href={jsonlUrl}
            download
            data-testid="audit-export-jsonl"
            className="h-7 rounded-md border border-border bg-surface px-2.5 text-[12px] font-medium text-fg hover:bg-surface-hover inline-flex items-center"
          >
            {t("audit.export.jsonl")}
          </a>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        {state === "loading" && entries.length === 0 ? (
          <p className="px-4 py-6 text-[12px] text-fg-muted">{t("audit.loading")}</p>
        ) : null}
        {state === "error" ? (
          <p
            role="alert"
            className="mx-4 my-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
          >
            {error}
          </p>
        ) : null}

        {state === "ready" && entries.length === 0 ? (
          <p className="px-4 py-6 text-center text-[12px] text-fg-muted">{t("audit.empty")}</p>
        ) : null}

        {entries.length > 0 ? (
          <table data-testid="audit-table" className="w-full table-auto text-[12px]">
            <thead className="sticky top-0 z-10 bg-bg-subtle text-left text-[10px] uppercase tracking-wide text-fg-muted">
              <tr>
                <th className="px-4 py-2 font-medium">{t("audit.col.ts")}</th>
                <th className="px-4 py-2 font-medium">{t("audit.col.action")}</th>
                <th className="px-4 py-2 font-medium">{t("audit.col.actor")}</th>
                <th className="px-4 py-2 font-medium">{t("audit.col.outcome")}</th>
                <th className="px-4 py-2 font-medium">{t("audit.col.exec_code")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {entries.map((entry) => (
                <tr key={entry.id} className={entry.outcome === "error" ? "bg-danger/5" : ""}>
                  <td className="px-4 py-1.5 text-fg-muted whitespace-nowrap">
                    <time dateTime={entry.ts}>{new Date(entry.ts).toLocaleString()}</time>
                  </td>
                  <td className="px-4 py-1.5">
                    <code className="rounded bg-bg-subtle px-1.5 py-0.5 text-[11px] text-fg">
                      {entry.action}
                    </code>
                  </td>
                  <td className="px-4 py-1.5 text-fg-muted">
                    <span>{entry.actor_type}</span>
                    {entry.actor_id ? (
                      <code className="ml-1.5 text-[10px] text-fg-subtle">
                        {entry.actor_id.slice(0, 8)}…
                      </code>
                    ) : null}
                  </td>
                  <td className="px-4 py-1.5">
                    <span className={entry.outcome === "error" ? "text-danger" : "text-success"}>
                      {entry.outcome}
                    </span>
                  </td>
                  <td className="px-4 py-1.5">
                    {entry.exec_code ? (
                      <span
                        title={execMessage(entry.exec_code)}
                        className="text-[11px] text-fg-muted"
                      >
                        {entry.exec_code}
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>

      {hasMore ? (
        <div className="border-t border-border bg-bg-elevated px-4 py-2 text-center">
          <button
            type="button"
            onClick={loadMore}
            disabled={state === "loading"}
            data-testid="audit-load-more"
            className="h-7 rounded-md border border-border bg-surface px-3 text-[12px] font-medium text-fg hover:bg-surface-hover disabled:opacity-50"
          >
            {t("audit.load_more")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
