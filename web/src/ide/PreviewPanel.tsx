import { useCallback, useEffect, useRef, useState } from "react";

import { useI18n } from "@/app/providers/i18n-context";

type Device = "desktop" | "tablet" | "phone";

const DEVICE_WIDTH: Record<Device, number | null> = {
  desktop: null, // fill parent
  tablet: 768,
  phone: 390,
};

const STORAGE_KEY_PREFIX = "gapt.ide.preview";

function storageKey(workspaceId: string): string {
  return `${STORAGE_KEY_PREFIX}.${workspaceId}`;
}

function readStored(workspaceId: string): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(storageKey(workspaceId)) ?? "";
}

function writeStored(workspaceId: string, url: string): void {
  if (typeof window === "undefined") return;
  if (url === "") window.localStorage.removeItem(storageKey(workspaceId));
  else window.localStorage.setItem(storageKey(workspaceId), url);
}

interface Props {
  workspaceId: string;
}

/** Preview panel — embeds an iframe pointed at a user-supplied URL.
 *
 * Plan §3.12 calls for the eventual `https://{slug}.preview.{domain}/`
 * subdomain that lands once M1-E1's Caddy is fully wired (Cycle 3.13
 * / M1-E4). Until then the user enters the URL manually — same UX
 * as Vite's "Network" hint. */
export function PreviewPanel({ workspaceId }: Props) {
  const { t } = useI18n();
  const [url, setUrl] = useState(() => readStored(workspaceId));
  const [device, setDevice] = useState<Device>("desktop");
  const [reloadKey, setReloadKey] = useState(0);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  useEffect(() => {
    writeStored(workspaceId, url);
  }, [workspaceId, url]);

  const refresh = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);

  const width = DEVICE_WIDTH[device];

  return (
    <div data-panel-kind="preview" className="flex h-full flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-3 py-2">
        <label className="flex flex-1 items-center gap-2">
          <span className="sr-only">{t("preview.url_label")}</span>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.currentTarget.value)}
            placeholder={t("preview.url_placeholder")}
            aria-label={t("preview.url_label")}
            className="h-7 w-full rounded-md border border-border bg-surface px-2 text-[12px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </label>
        <button
          type="button"
          onClick={refresh}
          disabled={url.length === 0}
          className="h-7 rounded-md border border-border bg-surface px-2.5 text-[12px] font-medium text-fg hover:bg-surface-hover disabled:opacity-50"
        >
          {t("preview.refresh")}
        </button>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          aria-disabled={url.length === 0}
          tabIndex={url.length === 0 ? -1 : 0}
          className="inline-flex h-7 items-center rounded-md border border-border bg-surface px-2.5 text-[12px] font-medium text-fg hover:bg-surface-hover aria-disabled:opacity-50"
        >
          {t("preview.open_external")}
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
            src={url}
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
