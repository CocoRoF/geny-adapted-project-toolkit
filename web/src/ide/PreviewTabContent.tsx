import { ExternalLink, RotateCw } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { useI18n } from "@/app/providers/i18n-context";

type Device = "desktop" | "tablet" | "phone";

const DEVICE_WIDTH: Record<Device, number | null> = {
  desktop: null,
  tablet: 768,
  phone: 390,
};

interface Props {
  initialUrl: string;
}

/** Embedded browser-style preview body for the editor multi-tab area
 * (VSCode Simple Browser parity).
 *
 * Self-contained: holds URL / device / reload state locally so two
 * preview tabs side-by-side stay independent. The iframe is the only
 * heavy child — `key={reloadKey}` is the explicit user-driven remount
 * mechanism (Refresh button). Outside of that, the iframe never
 * re-mounts on its own; that's what `EditorArea`'s display:none-not-
 * unmount strategy preserves across tab switches.
 *
 * URL bar is editable so the user can navigate inside the embedded
 * app (e.g. follow a link the iframe sandbox didn't allow). Hitting
 * Enter commits the typed URL. */
export function PreviewTabContent({ initialUrl }: Props) {
  const { t } = useI18n();
  const [url, setUrl] = useState(initialUrl);
  const [draft, setDraft] = useState(initialUrl);
  const [device, setDevice] = useState<Device>("desktop");
  const [reloadKey, setReloadKey] = useState(0);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  // Remounting the iframe alone re-issues the SAME document URL — the
  // browser may satisfy it (and its subresources) straight from
  // cache, which is exactly what the user is trying to escape when a
  // dev preview renders unstyled. A throwaway query param makes the
  // document URL unique so it always refetches; with the preview
  // routes now stamping `Cache-Control: no-store`, the fresh document
  // pulls fresh assets too.
  const refresh = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);

  const iframeSrc = useMemo(() => {
    if (!url || reloadKey === 0) return url;
    try {
      const u = new URL(url, window.location.origin);
      u.searchParams.set("_gapt_reload", String(reloadKey));
      return u.toString();
    } catch {
      return url;
    }
  }, [url, reloadKey]);

  const commitDraft = useCallback(() => {
    setUrl(draft.trim());
  }, [draft]);

  const width = DEVICE_WIDTH[device];

  return (
    <div data-panel-kind="preview" className="flex h-full flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-bg-elevated px-3 py-2">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            commitDraft();
          }}
          className="flex flex-1 items-center gap-2"
        >
          <label className="flex flex-1 items-center gap-2">
            <span className="sr-only">{t("preview.url_label")}</span>
            <input
              type="url"
              value={draft}
              onChange={(e) => setDraft(e.currentTarget.value)}
              onBlur={commitDraft}
              placeholder={t("preview.url_placeholder")}
              aria-label={t("preview.url_label")}
              className="h-7 w-full rounded-md border border-border bg-surface px-2 text-[12px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </label>
        </form>
        <button
          type="button"
          onClick={refresh}
          disabled={url.length === 0}
          title={t("preview.refresh")}
          aria-label={t("preview.refresh")}
          className="grid h-7 w-7 place-items-center rounded-md border border-border bg-surface text-fg hover:bg-surface-hover disabled:opacity-50"
        >
          <RotateCw className="h-3.5 w-3.5" />
        </button>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          aria-disabled={url.length === 0}
          tabIndex={url.length === 0 ? -1 : 0}
          title={t("preview.open_external")}
          aria-label={t("preview.open_external")}
          className="inline-grid h-7 w-7 place-items-center rounded-md border border-border bg-surface text-fg hover:bg-surface-hover aria-disabled:opacity-50"
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
        <div
          role="radiogroup"
          aria-label="device"
          className="ml-1 inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
        >
          {(["desktop", "tablet", "phone"] as const).map((d) => (
            <button
              key={d}
              type="button"
              role="radio"
              aria-checked={device === d}
              onClick={() => setDevice(d)}
              className={
                device === d
                  ? "rounded bg-bg px-2 py-0.5 text-[11px] font-medium text-fg shadow-sm"
                  : "rounded px-2 py-0.5 text-[11px] font-medium text-fg-muted hover:text-fg"
              }
            >
              {t(`preview.device.${d}`)}
            </button>
          ))}
        </div>
      </header>
      <div className="flex-1 overflow-hidden bg-bg-subtle">
        {url.length === 0 ? (
          <p className="grid h-full place-items-center text-[12px] text-fg-muted">
            {t("preview.empty")}
          </p>
        ) : (
          <iframe
            key={reloadKey}
            ref={iframeRef}
            src={iframeSrc}
            title={t("preview.title")}
            data-testid="preview-iframe"
            style={
              width
                ? {
                    width: `${width}px`,
                    height: "100%",
                    border: 0,
                    margin: "0 auto",
                    display: "block",
                    background: "white",
                  }
                : { width: "100%", height: "100%", border: 0, background: "white" }
            }
          />
        )}
      </div>
    </div>
  );
}
