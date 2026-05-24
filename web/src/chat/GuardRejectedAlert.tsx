import { AlertTriangle } from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";
import { Modal } from "@/ui/Modal";

interface Props {
  reason: string | null;
  onDismiss: () => void;
}

export function GuardRejectedAlert({ reason, onDismiss }: Props) {
  const { t, execMessage } = useI18n();
  const friendly = execMessage("exec.stage.guard_rejected");
  return (
    <Modal
      open
      onClose={onDismiss}
      title={t("cost.guard_rejected.title")}
      size="md"
      footer={
        <Button variant="primary" onClick={onDismiss}>
          {t("cost.guard_rejected.dismiss")}
        </Button>
      }
    >
      <div data-testid="guard-rejected" className="flex gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-warn" />
        <div className="space-y-2 text-[13px]">
          <p className="text-fg">{t("cost.guard_rejected.body")}</p>
          <p>
            <code className="rounded bg-bg-subtle px-1.5 py-0.5 text-[11px] text-warn">
              exec.stage.guard_rejected
            </code>{" "}
            <span className="text-fg-muted">{friendly}</span>
          </p>
          {reason ? (
            <pre className="max-h-32 overflow-auto rounded-md bg-bg-subtle px-3 py-2 text-[11px] text-fg-muted">
              {reason}
            </pre>
          ) : null}
        </div>
      </div>
    </Modal>
  );
}
