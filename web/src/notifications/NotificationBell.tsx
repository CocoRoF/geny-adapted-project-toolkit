import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { type Notification, listNotifications } from "@/api/notifications";
import { useI18n } from "@/app/providers/i18n-context";

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

/** Notification bell — header chip + dropdown list.
 *
 * Polls every 30s; refreshes on dropdown open. Pure ephemeral data
 * (the server ring buffer is the source of truth), no read/unread
 * persistence yet — the unread badge counts everything since the last
 * dropdown open. */
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

  const unread = items.filter((n) => n.ts > lastSeenRef.current).length;

  function toggle(): void {
    if (!open) {
      refresh();
      lastSeenRef.current = Date.now() / 1000;
    }
    setOpen(!open);
  }

  return (
    <div className="notification-bell">
      <button
        type="button"
        className="notification-bell-button"
        aria-label={t("notifications.title")}
        aria-expanded={open}
        onClick={toggle}
        data-testid="notification-bell"
      >
        <span aria-hidden>🔔</span>
        {unread > 0 ? (
          <span className="notification-bell-badge" data-testid="notification-badge">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>
      {open ? (
        <div
          className="notification-dropdown"
          role="dialog"
          aria-label={t("notifications.title")}
          data-testid="notification-dropdown"
        >
          <header>
            <h2>{t("notifications.title")}</h2>
            <button type="button" onClick={refresh} aria-label={t("notifications.refresh")}>
              ↻
            </button>
          </header>
          {error ? (
            <p role="alert" className="notification-dropdown-error">
              {error}
            </p>
          ) : null}
          {items.length === 0 && !error ? (
            <p className="notification-dropdown-empty">{t("notifications.empty")}</p>
          ) : null}
          <ul>
            {items.map((n) => (
              <li
                key={n.id}
                className={`notification-item notification-item--${n.severity}`}
                data-testid="notification-item"
              >
                <div className="notification-item-header">
                  <strong>{n.title}</strong>
                  <time dateTime={new Date(n.ts * 1000).toISOString()}>
                    {formatRelative(n.ts, locale)}
                  </time>
                </div>
                <p>{n.body}</p>
                <code className="notification-item-kind">{n.kind}</code>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
