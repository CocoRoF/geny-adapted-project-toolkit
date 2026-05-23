import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import {
  type CostSummary,
  type CostSummaryRow,
  type DailyCostRow,
  getCostSummary,
  getProjectCostDaily,
} from "@/api/cost";
import { useI18n } from "@/app/providers/i18n-context";

type LoadState = "loading" | "ready" | "error";

type RangePreset = "7d" | "30d" | "90d" | "all";

function isoForDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

function resolveSince(preset: RangePreset): string | undefined {
  if (preset === "all") return undefined;
  if (preset === "7d") return isoForDaysAgo(7);
  if (preset === "30d") return isoForDaysAgo(30);
  return isoForDaysAgo(90);
}

function formatCost(value: number): string {
  return `$${value.toFixed(4)}`;
}

/** Cost dashboard panel.
 *
 * Top section is a per-project totals table (always shown). When a
 * project is expanded, the panel also fetches `/cost/daily` for that
 * project and renders CSS bars relative to the max-day cost. We pick
 * CSS bars over recharts to keep the bundle small — Plan 3.10 calls
 * for recharts but it's deferred until the design refresh. */
export function CostPanel() {
  const { t } = useI18n();
  const [preset, setPreset] = useState<RangePreset>("30d");
  const [state, setState] = useState<LoadState>("loading");
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [daily, setDaily] = useState<DailyCostRow[]>([]);
  const [dailyState, setDailyState] = useState<LoadState>("ready");

  const since = useMemo(() => resolveSince(preset), [preset]);

  const refresh = useCallback(() => {
    setState("loading");
    getCostSummary(since ? { since } : {})
      .then((s) => {
        setSummary(s);
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
  }, [since]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (expanded === null) {
      setDaily([]);
      return;
    }
    setDailyState("loading");
    getProjectCostDaily(expanded, since ? { since } : {})
      .then((rows) => {
        setDaily(rows);
        setDailyState("ready");
      })
      .catch(() => {
        setDaily([]);
        setDailyState("error");
      });
  }, [expanded, since]);

  const maxDaily = daily.reduce((a, r) => Math.max(a, r.cost_usd), 0) || 1;

  return (
    <div className="cost-panel" data-panel-kind="cost">
      <header className="cost-panel-header">
        <h3>{t("cost.dashboard.title")}</h3>
        <div className="cost-panel-filters">
          <label>
            <span>{t("cost.range.label")}</span>
            <select
              value={preset}
              onChange={(e) => setPreset(e.currentTarget.value as RangePreset)}
            >
              <option value="7d">{t("cost.range.7d")}</option>
              <option value="30d">{t("cost.range.30d")}</option>
              <option value="90d">{t("cost.range.90d")}</option>
              <option value="all">{t("cost.range.all")}</option>
            </select>
          </label>
          <button type="button" onClick={refresh} disabled={state === "loading"}>
            {t("cost.refresh")}
          </button>
        </div>
      </header>

      {state === "loading" && summary === null ? <p>{t("cost.loading")}</p> : null}
      {state === "error" ? (
        <p role="alert" className="cost-panel-error">
          {error}
        </p>
      ) : null}

      {summary !== null && summary.rows.length === 0 && state === "ready" ? (
        <p>{t("cost.empty")}</p>
      ) : null}

      {summary !== null && summary.rows.length > 0 ? (
        <>
          <dl className="cost-summary-totals" data-testid="cost-totals">
            <dt>{t("cost.totals.cost")}</dt>
            <dd>{formatCost(summary.total_cost_usd)}</dd>
            <dt>{t("cost.totals.input_tokens")}</dt>
            <dd>{summary.total_input_tokens.toLocaleString()}</dd>
            <dt>{t("cost.totals.output_tokens")}</dt>
            <dd>{summary.total_output_tokens.toLocaleString()}</dd>
          </dl>

          <table className="cost-table" data-testid="cost-table">
            <thead>
              <tr>
                <th>{t("cost.col.project")}</th>
                <th>{t("cost.col.cost")}</th>
                <th>{t("cost.col.tokens_in")}</th>
                <th>{t("cost.col.tokens_out")}</th>
                <th>{t("cost.col.sessions")}</th>
              </tr>
            </thead>
            <tbody>
              {summary.rows.map((row: CostSummaryRow) => (
                <tr
                  key={row.project_id}
                  className={expanded === row.project_id ? "cost-row cost-row--open" : "cost-row"}
                >
                  <td>
                    <button
                      type="button"
                      className="cost-project-button"
                      onClick={() =>
                        setExpanded(expanded === row.project_id ? null : row.project_id)
                      }
                      data-testid={`cost-row-${row.project_slug}`}
                    >
                      {row.project_display_name}
                      <code>{row.project_slug}</code>
                    </button>
                  </td>
                  <td>{formatCost(row.cost_usd)}</td>
                  <td>{row.input_tokens.toLocaleString()}</td>
                  <td>{row.output_tokens.toLocaleString()}</td>
                  <td>{row.session_count}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {expanded !== null ? (
            <section className="cost-daily" data-testid="cost-daily">
              <h4>{t("cost.daily.title")}</h4>
              {dailyState === "loading" ? <p>{t("cost.loading")}</p> : null}
              {dailyState === "error" ? <p role="alert">{t("cost.daily.error")}</p> : null}
              {dailyState === "ready" && daily.length === 0 ? <p>{t("cost.daily.empty")}</p> : null}
              {daily.map((row) => (
                <div key={row.date} className="cost-daily-row">
                  <span className="cost-daily-date">{row.date}</span>
                  <span
                    className="cost-daily-bar"
                    style={{ width: `${(row.cost_usd / maxDaily) * 100}%` }}
                    aria-hidden
                  />
                  <span className="cost-daily-value">{formatCost(row.cost_usd)}</span>
                </div>
              ))}
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
