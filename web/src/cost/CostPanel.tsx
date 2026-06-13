import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type CostSummary,
  type CostSummaryRow,
  type DailyCostRow,
  getCostSummary,
  getProjectCostDaily,
} from "@/api/cost";
import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";
import { Select } from "@/ui/Input";
import { cn } from "@/ui/cn";

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

function fmtUsd(value: number): string {
  // Sign belongs OUTSIDE the currency symbol: "-$0.8560", not the
  // "$-0.8560" the naive template produced. Negative totals can occur
  // from a pricing-alias miss producing a refund-shaped delta.
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toFixed(4)}`;
}

/** Cost dashboard panel — totals, per-project table, expandable
 * daily breakdown rendered as CSS bars (no chart lib in the bundle). */
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
    <div data-panel-kind="cost" className="flex h-full flex-col gap-4 overflow-y-auto p-5">
      <header className="flex items-end justify-between gap-3 border-b border-border pb-3">
        <div>
          <h2 className="text-[16px] font-semibold tracking-tight text-fg">
            {t("cost.dashboard.title")}
          </h2>
          <p className="mt-0.5 text-[12px] text-fg-muted">
            {t("cost.range.label")}: {t(`cost.range.${preset}`)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={preset}
            onChange={(e) => setPreset(e.currentTarget.value as RangePreset)}
            aria-label={t("cost.range.label")}
            className="w-[140px]"
          >
            <option value="7d">{t("cost.range.7d")}</option>
            <option value="30d">{t("cost.range.30d")}</option>
            <option value="90d">{t("cost.range.90d")}</option>
            <option value="all">{t("cost.range.all")}</option>
          </Select>
          <Button variant="outline" size="md" onClick={refresh} disabled={state === "loading"}>
            <RefreshCw
              className={state === "loading" ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"}
            />
            {t("cost.refresh")}
          </Button>
        </div>
      </header>

      {state === "loading" && summary === null ? (
        <p className="text-[13px] text-fg-muted">{t("cost.loading")}</p>
      ) : null}
      {state === "error" ? (
        <p
          role="alert"
          className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[13px] text-danger"
        >
          {error}
        </p>
      ) : null}

      {summary !== null && summary.rows.length === 0 && state === "ready" ? (
        <div className="rounded-lg border border-dashed border-border p-8 text-center text-[13px] text-fg-muted">
          {t("cost.empty")}
        </div>
      ) : null}

      {summary !== null && summary.rows.length > 0 ? (
        <>
          {/* Totals card */}
          <div
            data-testid="cost-totals"
            className="grid grid-cols-3 gap-3 rounded-lg border border-border bg-bg-elevated p-4"
          >
            <Stat label={t("cost.totals.cost")} value={fmtUsd(summary.total_cost_usd)} accent />
            <Stat
              label={t("cost.totals.input_tokens")}
              value={summary.total_input_tokens.toLocaleString()}
            />
            <Stat
              label={t("cost.totals.output_tokens")}
              value={summary.total_output_tokens.toLocaleString()}
            />
          </div>

          {/* Per-project table */}
          <div className="overflow-hidden rounded-lg border border-border">
            <table data-testid="cost-table" className="w-full table-auto text-[13px]">
              <thead className="bg-bg-subtle text-left text-[11px] uppercase tracking-wide text-fg-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">{t("cost.col.project")}</th>
                  <th className="px-3 py-2 font-medium text-right">{t("cost.col.cost")}</th>
                  <th className="px-3 py-2 font-medium text-right">{t("cost.col.tokens_in")}</th>
                  <th className="px-3 py-2 font-medium text-right">{t("cost.col.tokens_out")}</th>
                  <th className="px-3 py-2 font-medium text-right">{t("cost.col.sessions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border bg-bg-elevated">
                {summary.rows.map((row: CostSummaryRow) => {
                  const isOpen = expanded === row.project_id;
                  return (
                    <tr
                      key={row.project_id}
                      className={cn("transition-colors", isOpen && "bg-surface-hover")}
                    >
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => setExpanded(isOpen ? null : row.project_id)}
                          data-testid={`cost-row-${row.project_slug}`}
                          className="flex items-center gap-2 text-left hover:text-accent"
                        >
                          {isOpen ? (
                            <ChevronDown className="h-3.5 w-3.5 text-fg-muted" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5 text-fg-muted" />
                          )}
                          <span className="font-medium">{row.project_display_name}</span>
                          <code className="text-[11px] text-fg-subtle">{row.project_slug}</code>
                        </button>
                      </td>
                      <td className="px-3 py-2 text-right font-mono tabular-nums">
                        {fmtUsd(row.cost_usd)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono tabular-nums text-fg-muted">
                        {row.input_tokens.toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-right font-mono tabular-nums text-fg-muted">
                        {row.output_tokens.toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-right font-mono tabular-nums text-fg-muted">
                        {row.session_count}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Daily breakdown */}
          {expanded !== null ? (
            <section
              data-testid="cost-daily"
              className="rounded-lg border border-border bg-bg-elevated p-4"
            >
              <h3 className="mb-3 text-[13px] font-semibold text-fg">{t("cost.daily.title")}</h3>
              {dailyState === "loading" ? (
                <p className="text-[12px] text-fg-muted">{t("cost.loading")}</p>
              ) : null}
              {dailyState === "error" ? (
                <p role="alert" className="text-[12px] text-danger">
                  {t("cost.daily.error")}
                </p>
              ) : null}
              {dailyState === "ready" && daily.length === 0 ? (
                <p className="text-[12px] text-fg-muted">{t("cost.daily.empty")}</p>
              ) : null}
              <ul className="space-y-1.5">
                {daily.map((row) => (
                  <li key={row.date} className="flex items-center gap-3 text-[12px]">
                    <span className="w-24 font-mono tabular-nums text-fg-muted">{row.date}</span>
                    <div className="flex h-2 flex-1 overflow-hidden rounded-full bg-bg-subtle">
                      <div
                        className="bg-accent transition-all"
                        style={{
                          width: `${Math.max(0, Math.min(100, (row.cost_usd / maxDaily) * 100))}%`,
                        }}
                        aria-hidden
                      />
                    </div>
                    <span className="w-20 text-right font-mono tabular-nums">
                      {fmtUsd(row.cost_usd)}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function Stat({
  label,
  value,
  accent = false,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-fg-muted">{label}</div>
      <div
        className={cn(
          "mt-1 text-[18px] font-semibold tabular-nums",
          accent ? "text-accent" : "text-fg",
        )}
      >
        {value}
      </div>
    </div>
  );
}
