import { AlertTriangle } from "lucide-react";

import { Button } from "@/ui/Button";
import { Modal } from "@/ui/Modal";

interface Props {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel: string;
  /** Use the `danger` variant for destructive actions. */
  tone?: "neutral" | "danger";
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Lightweight destructive-action confirmation. The icon turns red
 * when tone="danger" — both delete project and delete workspace use
 * that path. Esc + backdrop click cancel automatically (Modal). */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel,
  tone = "neutral",
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) onCancel();
      }}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={tone === "danger" ? "danger" : "primary"}
            onClick={onConfirm}
            disabled={busy}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-3">
        <AlertTriangle
          className={
            tone === "danger"
              ? "mt-0.5 h-5 w-5 shrink-0 text-danger"
              : "mt-0.5 h-5 w-5 shrink-0 text-warn"
          }
        />
        <p className="text-[13px] text-fg-muted">{description}</p>
      </div>
    </Modal>
  );
}
