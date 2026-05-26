import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type WorkspaceService, listServices } from "@/api/services";
import { useI18n } from "@/app/providers/i18n-context";

type Device = "desktop" | "tablet" | "phone";

const DEVICE_WIDTH: Record<Device, number | null> = {
  desktop: null, // fill parent
  tablet: 768,
  phone: 390,
};

const STORAGE_KEY_PREFIX = "gapt.ide.preview";
const SERVICES_POLL_MS = 4000;

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

/** Preview panel — embeds an iframe pointed at a workspace dev server.
 *
 * Two ways to populate the URL:
 *   1. **Exposed-service dropdown** — picks from services the user
 *      already hit "Expose" on in the Service tab. The selected
 *      service's `bound_url` becomes the iframe src. The first
 *      exposed service auto-selects so the user sees the preview
 *      without an extra click.
 *   2. **Manual entry** — for arbitrary URLs (e.g. a remote staging
 *      env, or a public site you're integrating with).
 *
 * The iframe never re-mounts on sibling-panel actions (the previous
 * `reloadNonce` mechanism caused a visible blank flash on Expose
 * because the iframe is the size of the screen). The user reloads
 * the preview explicitly via the Refresh button in this header. */
export function PreviewPanel({ workspaceId }: Props) {
  const { t } = useI18n();
  const [url, setUrl] = useState(() => readStored(workspaceId));
  const [device, setDevice] = useState<Device>("desktop");
  const [reloadKey, setReloadKey] = useState(0);
  const [services, setServices] = useState<WorkspaceService[]>([]);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  useEffect(() => {
    writeStored(workspaceId, url);
  }, [workspaceId, url]);

  // Poll the services list so the dropdown picks up newly exposed
  // services without forcing the user to reload the page.
  useEffect(() => {
    let cancelled = false;
    const pull = () =>
      listServices(workspaceId)
        .then((rows) => {
          if (!cancelled) setServices(rows);
        })
        .catch(() => {
          // 404/transient is fine — the dropdown just stays empty.
        });
    pull();
    const id = window.setInterval(pull, SERVICES_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [workspaceId]);

  const exposed = useMemo(
    () =>
      services.filter(
        (s): s is WorkspaceService & { bound_url: string } => !!s.bound_url,
      ),
    [services],
  );

  // Auto-select the first exposed service ONCE — when the iframe is
  // empty (no stored URL) and at least one service just became
  // exposed, point the iframe at it. The user clicked Expose; this
  // is what they want to see. Doesn't reset their URL if they later
  // pick a different service or type a custom one.
  useEffect(() => {
    if (url.length > 0) return;
    if (exposed.length === 0) return;
    setUrl(exposed[0].bound_url);
  }, [exposed, url]);

  const refresh = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);

  const width = DEVICE_WIDTH[device];

  return (
    <div data-panel-kind="preview" className="flex h-full flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-bg-elevated px-3 py-2">
        {exposed.length > 0 ? (
          <select
            aria-label={t("preview.exposed.aria_label")}
            value={exposed.find((s) => s.bound_url === url)?.label ?? ""}
            onChange={(e) => {
              const next = exposed.find((s) => s.label === e.currentTarget.value);
              if (next) setUrl(next.bound_url);
            }}
            className="h-7 max-w-[200px] rounded-md border border-border bg-surface px-2 text-[12px] text-fg focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="" disabled>
              {t("preview.exposed.placeholder")}
            </option>
            {exposed.map((s) => (
              <option key={s.label} value={s.label}>
                {s.label}
                {s.port ? ` :${s.port}` : ""}
              </option>
            ))}
          </select>
        ) : null}
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
