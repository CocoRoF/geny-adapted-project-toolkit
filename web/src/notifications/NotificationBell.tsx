import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, RefreshCw } from "lucide-react";

import { ApiError } from "@/api/client";
import { type Notification, listNotifications } from "@/api/notifications";
import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

const POLL_INTERVAL_MS = 30_000;

function formatRelative(ts: number, locale: string): string {
  const diffSec = Math.round(ts - Date.now() / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const abs = Math.abs(diffSec);
  if (abs < 60) return rtf.format(diffSec, "second");
  if (abs < 3600) return rtf.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), "hour");
  return rtf.format(Math.round(diffSec / 86400), "day");
}

const SEVERITY_DOT: Record<Notification["severity"], string> = {
  info: "bg-accent",
  warn: "bg-warn",
  error: "bg-danger",
};

/** Notification bell — header chip + dropdown list.
 *
 * Polls every 30s; refreshes on dropdown open. Pure ephemeral data
 * (server ring buffer is the source of truth); the unread badge counts
 * everything since the last dropdown open. */
export function NotificationBell() {
  const { t, locale } = useI18n();
  const [items, setItems] = useState<Notification[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastSeenRef = useRef<number>(0);

  const refresh = useCallback(() => {
    listNotifications()
      .then((rows) => {
        setItems(Array.isArray(rows) ? rows : []);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof ApiError
            ? `${err.code}: ${err.reason}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      });
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  // Close on outside click — keeps the dropdown lightweight (no portal).
  const wrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const unread = items.filter((n) => n.ts > lastSeenRef.current).length;

  function toggle(): void {
    if (!open) {
      refresh();
      lastSeenRef.current = Date.now() / 1000;
    }
    setOpen(!open);
  }

  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="ghost"
        size="icon"
        aria-label={t("notifications.title")}
        aria-expanded={open}
        onClick={toggle}
        data-testid="notification-bell"
        className="relative"
      >
        <Bell className="h-4 w-4" />
        {unread > 0 ? (
          <span
            data-testid="notification-badge"
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold text-white shadow-sm"
          >
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </Button>

      {open ? (
        <div
          role="dialog"
          aria-label={t("notifications.title")}
          data-testid="notification-dropdown"
          className="absolute right-0 top-[calc(100%+6px)] z-40 w-[360px] overflow-hidden rounded-lg border border-border bg-bg-elevated shadow-xl"
        >
          <header className="flex items-center justify-between border-b border-border px-3 py-2">
            <h2 className="text-[13px] font-semibold text-fg">{t("notifications.title")}</h2>
            <Button
              variant="ghost"
              size="icon"
              onClick={refresh}
              aria-label={t("notifications.refresh")}
              title={t("notifications.refresh")}
              className="h-6 w-6"
            >
              <RefreshCw className="h-3 w-3" />
            </Button>
          </header>

          {error ? (
            <p role="alert" className="px-3 py-2 text-[12px] text-danger">
              {error}
            </p>
          ) : null}

          {items.length === 0 && !error ? (
            <p className="px-3 py-6 text-center text-[12px] text-fg-muted">
              {t("notifications.empty")}
            </p>
          ) : null}

          <ul className="max-h-[420px] divide-y divide-border overflow-y-auto">
            {items.map((n) => (
              <li
                key={n.id}
                data-testid="notification-item"
                className="px-3 py-2.5 transition-colors hover:bg-surface-hover"
              >
                <div className="flex items-start gap-2">
                  <span
                    aria-hidden
                    className={cn(
                      "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                      SEVERITY_DOT[n.severity] ?? "bg-fg-subtle",
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <strong className="truncate text-[13px] font-semibold text-fg">
                        {n.title}
                      </strong>
                      <time
                        dateTime={new Date(n.ts * 1000).toISOString()}
                        className="shrink-0 text-[11px] text-fg-subtle"
                      >
                        {formatRelative(n.ts, locale)}
                      </time>
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-[12px] text-fg-muted">{n.body}</p>
                    <code className="mt-1 inline-block rounded bg-bg-subtle px-1.5 py-0.5 text-[10px] text-fg-muted">
                      {n.kind}
                    </code>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
