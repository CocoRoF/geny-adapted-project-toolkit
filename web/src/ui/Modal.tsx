import { useEffect, type ReactNode } from "react";

import { cn } from "@/ui/cn";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  /** width preset — sm 360, md 480, lg 640. */
  size?: "sm" | "md" | "lg";
}

/** Backdrop + centered card. Esc closes; click outside closes. */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  const width = size === "sm" ? "max-w-[360px]" : size === "lg" ? "max-w-[640px]" : "max-w-[480px]";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className={cn("w-full rounded-lg border border-border bg-bg-elevated shadow-xl", width)}
      >
        {title || description ? (
          <header className="border-b border-border px-4 py-3">
            {title ? <h2 className="text-[15px] font-semibold text-fg">{title}</h2> : null}
            {description ? <p className="mt-0.5 text-[12px] text-fg-muted">{description}</p> : null}
          </header>
        ) : null}
        <div className="px-4 py-4">{children}</div>
        {footer ? (
          <footer className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}
