import { useI18n } from "@/app/providers/i18n-context";
import { type CostSnapshot, formatMs } from "@/chat/cost-snapshot";

interface Props {
  snapshot: CostSnapshot;
  onClose: () => void;
}

/** Modal that breaks down the live cost snapshot.
 *
 * Plan §3.10 calls for daily charts (recharts) — pulled forward to a
 * later cycle to keep the bundle small. The numbers we surface today
 * (session-cumulative cost + per-tool counts) are the ones the live
 * header doesn't show. */
export function CostModal({ snapshot, onClose }: Props) {
  const { t } = useI18n();
  const tools = Object.entries(snapshot.by_tool).sort((a, b) => b[1] - a[1]);

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="cost-modal-title" className="modal">
      <div className="modal-content cost-modal-content">
        <header className="cost-modal-header">
          <h2 id="cost-modal-title">{t("cost.title")}</h2>
          <button type="button" onClick={onClose} aria-label={t("cost.close")}>
            ×
          </button>
        </header>

        <dl className="cost-modal-grid">
          <dt>{t("cost.session_total")}</dt>
          <dd data-testid="cost-modal-total">${snapshot.cost_usd.toFixed(4)}</dd>

          <dt>{t("cost.tokens.input")}</dt>
          <dd>{snapshot.input_tokens.toLocaleString()}</dd>

          <dt>{t("cost.tokens.output")}</dt>
          <dd>{snapshot.output_tokens.toLocaleString()}</dd>

          <dt>{t("cost.tool_calls")}</dt>
          <dd>{snapshot.tool_calls}</dd>

          <dt>{t("cost.tool_duration")}</dt>
          <dd>{formatMs(snapshot.tool_duration_ms)}</dd>
        </dl>

        <section className="cost-modal-tools">
          <h3>{t("cost.by_tool")}</h3>
          {tools.length === 0 ? (
            <p className="cost-modal-no-tools">{t("cost.no_tools")}</p>
          ) : (
            <ul data-testid="cost-modal-tools">
              {tools.map(([name, count]) => (
                <li key={name}>
                  <code>{name}</code>
                  <span>{count}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <footer className="cost-modal-footer">
          <button type="button" onClick={onClose}>
            {t("cost.close")}
          </button>
        </footer>
      </div>
    </div>
  );
}
