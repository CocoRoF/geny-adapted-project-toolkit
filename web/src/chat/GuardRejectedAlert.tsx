import { useI18n } from "@/app/providers/i18n-context";

interface Props {
  reason: string | null;
  onDismiss: () => void;
}

/** Modal banner shown when the session emits an
 * `exec.stage.guard_rejected` error — the agent hit a configured
 * budget / policy ceiling. The user gets to see why and how to
 * loosen the limit (the link target lands in M1-E4 docs). */
export function GuardRejectedAlert({ reason, onDismiss }: Props) {
  const { t, execMessage } = useI18n();
  const friendly = execMessage("exec.stage.guard_rejected");
  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="guard-rejected-title"
      className="modal modal--alert"
      data-testid="guard-rejected"
    >
      <div className="modal-content modal-content--alert">
        <h2 id="guard-rejected-title">{t("cost.guard_rejected.title")}</h2>
        <p>{t("cost.guard_rejected.body")}</p>
        <p>
          <strong>exec.stage.guard_rejected</strong>: {friendly}
        </p>
        {reason ? <pre className="guard-rejected-reason">{reason}</pre> : null}
        <button type="button" onClick={onDismiss}>
          {t("cost.guard_rejected.dismiss")}
        </button>
      </div>
    </div>
  );
}
