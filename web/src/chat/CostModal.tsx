import { useI18n } from "@/app/providers/i18n-context";
import { type CostSnapshot, formatMs } from "@/chat/cost-snapshot";
import { Button } from "@/ui/Button";
import { Modal } from "@/ui/Modal";

interface Props {
  snapshot: CostSnapshot;
  onClose: () => void;
}

export function CostModal({ snapshot, onClose }: Props) {
  const { t } = useI18n();
  const tools = Object.entries(snapshot.by_tool).sort((a, b) => b[1] - a[1]);

  return (
    <Modal
      open
      onClose={onClose}
      title={t("cost.title")}
      size="md"
      footer={
        <Button variant="secondary" onClick={onClose}>
          {t("cost.close")}
        </Button>
      }
    >
      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-[13px]">
        <dt className="text-fg-muted">{t("cost.session_total")}</dt>
        <dd
          data-testid="cost-modal-total"
          className="font-mono tabular-nums font-semibold text-accent"
        >
          ${snapshot.cost_usd.toFixed(4)}
        </dd>

        <dt className="text-fg-muted">{t("cost.tokens.input")}</dt>
        <dd className="font-mono tabular-nums">{snapshot.input_tokens.toLocaleString()}</dd>

        <dt className="text-fg-muted">{t("cost.tokens.output")}</dt>
        <dd className="font-mono tabular-nums">{snapshot.output_tokens.toLocaleString()}</dd>

        {/* Phase K.2 — only show cache rows when non-zero so the
            modal stays compact for short turns. */}
        {snapshot.cache_write_tokens > 0 ? (
          <>
            <dt
              className="cursor-help text-fg-muted underline decoration-dotted underline-offset-2"
              title={t("cost.tokens.cache_write.tooltip")}
            >
              {t("cost.tokens.cache_write")}
            </dt>
            <dd className="font-mono tabular-nums">
              {snapshot.cache_write_tokens.toLocaleString()}
            </dd>
          </>
        ) : null}
        {snapshot.cache_read_tokens > 0 ? (
          <>
            <dt
              className="cursor-help text-fg-muted underline decoration-dotted underline-offset-2"
              title={t("cost.tokens.cache_read.tooltip")}
            >
              {t("cost.tokens.cache_read")}
            </dt>
            <dd className="font-mono tabular-nums">
              {snapshot.cache_read_tokens.toLocaleString()}
            </dd>
          </>
        ) : null}

        <dt className="text-fg-muted">{t("cost.tool_calls")}</dt>
        <dd className="font-mono tabular-nums">{snapshot.tool_calls}</dd>

        <dt className="text-fg-muted">{t("cost.tool_duration")}</dt>
        <dd className="font-mono tabular-nums">{formatMs(snapshot.tool_duration_ms)}</dd>
      </dl>

      <section className="mt-5 border-t border-border pt-4">
        <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-fg-muted">
          {t("cost.by_tool")}
        </h3>
        {tools.length === 0 ? (
          <p className="text-[12px] text-fg-muted">{t("cost.no_tools")}</p>
        ) : (
          <ul
            data-testid="cost-modal-tools"
            className="divide-y divide-border rounded-md border border-border"
          >
            {tools.map(([name, count]) => (
              <li key={name} className="flex items-center justify-between px-3 py-1.5 text-[12px]">
                <code className="text-fg">{name}</code>
                <span className="font-mono tabular-nums text-fg-muted">{count}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </Modal>
  );
}
