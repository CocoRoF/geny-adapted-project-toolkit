import { useEffect, useState } from "react";

import { getCapabilities, type CapabilityReport } from "@/api/system";
import { useI18n } from "@/app/providers/i18n-context";

/** Warns when the host can't run workspace sandboxes (missing Docker
 * CLI / daemon / sysbox runtime / workspace image). Fetched once on
 * mount; renders nothing when everything is ready, the probe fails, or
 * the operator dismisses it. The per-capability label/detail/remedy
 * text comes from the backend (operator-facing diagnostics) — only the
 * banner chrome is localised. */
export function CapabilityBanner() {
  const { t } = useI18n();
  const [report, setReport] = useState<CapabilityReport | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;
    getCapabilities()
      .then((r) => {
        if (alive) setReport(r);
      })
      .catch(() => {
        // Probe unavailable (not logged in yet, server down) — stay silent.
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!report || report.workspaces_ready || dismissed) return null;
  const missing = report.capabilities.filter((c) => c.state !== "ok");

  return (
    <div
      role="alert"
      className="flex shrink-0 items-start gap-3 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-[13px] text-amber-100"
    >
      <div className="min-w-0 flex-1">
        <div className="font-medium text-amber-200">{t("capabilities.title")}</div>
        <ul className="mt-1 space-y-1">
          {missing.map((c) => (
            <li key={c.key} className="leading-snug">
              <span className="font-medium">{c.label}</span>
              <span className="text-amber-100/80"> — {c.detail}</span>
              {c.remedy ? <div className="text-amber-100/70">{c.remedy}</div> : null}
            </li>
          ))}
        </ul>
      </div>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        className="shrink-0 rounded px-2 py-0.5 text-amber-200/80 hover:bg-amber-500/20 hover:text-amber-100"
        aria-label={t("app.close")}
      >
        {t("app.close")}
      </button>
    </div>
  );
}
