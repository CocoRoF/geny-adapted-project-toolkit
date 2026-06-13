import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import { cn } from "@/ui/cn";

interface Props {
  src: string;
  alt: string;
  /** Classes for the inline thumbnail <img>. */
  className?: string;
  title?: string;
}

/** Thumbnail that opens a full-size lightbox on click.
 *
 * Used for chat image attachments (composer strip + sent bubbles):
 * the inline rendering is a small `object-cover` square, so without
 * this the user could never actually SEE what they attached. The
 * overlay is portal-mounted on <body> (escapes the panel's
 * overflow-hidden), closes on backdrop click, the X button, or ESC. */
export function PreviewableImage({ src, alt, className, title }: Props) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    // Capture phase so the chat panel's own ESC handler (interrupt)
    // doesn't fire while the lightbox is the topmost layer.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={title ?? alt}
        aria-label={t("lightbox.view_larger").replace("{name}", alt)}
        className="group/preview block cursor-zoom-in focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <img
          src={src}
          alt={alt}
          className={cn("transition-opacity group-hover/preview:opacity-90", className)}
        />
      </button>
      {open
        ? createPortal(
            <div
              role="dialog"
              aria-modal="true"
              aria-label={alt}
              className="fixed inset-0 z-[100] grid place-items-center bg-black/80 p-6"
              onClick={() => setOpen(false)}
            >
              <img
                src={src}
                alt={alt}
                // Stop propagation so clicking the image itself
                // doesn't dismiss — only the backdrop does.
                onClick={(e) => e.stopPropagation()}
                className="max-h-[88vh] max-w-[92vw] rounded-md object-contain shadow-2xl"
              />
              <button
                type="button"
                aria-label={t("app.close")}
                onClick={() => setOpen(false)}
                className="absolute right-4 top-4 grid h-9 w-9 place-items-center rounded-full bg-black/60 text-white hover:bg-black/80"
              >
                <X className="h-5 w-5" />
              </button>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
