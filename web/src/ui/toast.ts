import { toast } from "sonner";

interface ConfirmToastOptions {
  title: string;
  description?: string;
  confirmLabel: string;
  cancelLabel: string;
  /** "danger" renders the confirm action in destructive red. */
  tone?: "danger" | "default";
  onConfirm: () => void;
}

/** Action-toast replacement for `window.confirm` — non-blocking, themed,
 * keyboard-dismissable. The destructive action only fires on the
 * explicit confirm button; dismissing (timeout, ESC, close button)
 * is a no-op, which matches confirm()'s cancel semantics. */
export function confirmToast({
  title,
  description,
  confirmLabel,
  cancelLabel,
  tone = "default",
  onConfirm,
}: ConfirmToastOptions): void {
  toast(title, {
    ...(description ? { description } : {}),
    duration: 10_000,
    action: {
      label: confirmLabel,
      onClick: onConfirm,
    },
    cancel: {
      label: cancelLabel,
      onClick: () => undefined,
    },
    ...(tone === "danger"
      ? {
          actionButtonStyle: {
            backgroundColor: "var(--destructive, #dc2626)",
            color: "#fff",
          },
        }
      : {}),
  });
}

export { toast };
