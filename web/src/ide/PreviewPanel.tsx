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
    <div className="ide-preview" data-panel-kind="preview">
      <header className="ide-preview-header">
        <label className="ide-preview-url">
          <span className="sr-only">{t("preview.url_label")}</span>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.currentTarget.value)}
            placeholder={t("preview.url_placeholder")}
            aria-label={t("preview.url_label")}
          />
        </label>
        <div className="ide-preview-actions">
          <button type="button" onClick={refresh} disabled={url.length === 0}>
            {t("preview.refresh")}
          </button>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            aria-disabled={url.length === 0}
            tabIndex={url.length === 0 ? -1 : 0}
          >
            {t("preview.open_external")}
          </a>
        </div>
        <div className="ide-preview-devices" role="radiogroup" aria-label="device">
          {(["desktop", "tablet", "phone"] as const).map((d) => (
            <button
              key={d}
              type="button"
              role="radio"
              aria-checked={device === d}
              onClick={() => setDevice(d)}
              className={device === d ? "is-active" : undefined}
            >
              {t(`preview.device.${d}`)}
            </button>
          ))}
        </div>
      </header>
      <div className="ide-preview-body">
        {url.length === 0 ? (
          <p className="ide-preview-empty">{t("preview.empty")}</p>
        ) : (
          <iframe
            key={reloadKey}
            ref={iframeRef}
            src={url}
            title={t("preview.title")}
            data-testid="preview-iframe"
            // No sandbox flags so the previewed app keeps its full
            // capabilities — the iframe is for *the user's own*
            // sandboxed dev server, not for arbitrary third-party
            // content. The same-origin trust model is the user's
            // network reach.
            style={
              width
                ? {
                    width: `${width}px`,
                    height: "100%",
                    border: 0,
                    margin: "0 auto",
                    display: "block",
                  }
                : { width: "100%", height: "100%", border: 0 }
            }
          />
        )}
      </div>
    </div>
  );
}
