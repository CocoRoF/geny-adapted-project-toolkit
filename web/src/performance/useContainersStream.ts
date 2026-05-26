import { useEffect, useRef, useState } from "react";

import type { ContainersResponse } from "@/api/performance";

export type StreamState = "idle" | "connecting" | "open" | "paused" | "error";

interface UseContainersStreamResult {
  data: ContainersResponse | null;
  state: StreamState;
  error: string | null;
  /** Server-side ticks the client has seen since mount. Useful for
   * "live indicator" pulses without re-rendering the whole page. */
  tickCount: number;
}

/** Subscribes to `/api/performance/stream` (SSE) while:
 *   1. the component is mounted, AND
 *   2. `document.visibilityState === "visible"`.
 *
 * When the tab is hidden, the EventSource is closed so the server's
 * fan-out broadcaster cancels its sampling loop. When the tab
 * returns to visible, we reconnect. This means a Performance dash
 * left open in a background tab contributes ZERO load.
 *
 * Robust to:
 *   - server disconnects (browser's EventSource auto-reconnects;
 *     we cap our own attempts via the visibility gate)
 *   - rapid tab switches (next-tick close + reopen, no leaked
 *     connection)
 *   - StrictMode double-effect (the cleanup closes the prior source
 *     before the second effect opens its own)
 */
export function useContainersStream(): UseContainersStreamResult {
  const [data, setData] = useState<ContainersResponse | null>(null);
  const [state, setState] = useState<StreamState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [tickCount, setTickCount] = useState(0);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let cancelled = false;

    const close = () => {
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
    };

    const open = () => {
      if (cancelled || sourceRef.current) return;
      setState("connecting");
      setError(null);
      // Same-origin SSE; the session cookie ships automatically.
      // EventSource does *not* let us set headers, so cookie auth
      // is the only sensible option (matches the rest of the API).
      const src = new EventSource("/api/performance/stream", {
        withCredentials: true,
      });
      sourceRef.current = src;
      src.onopen = () => {
        if (cancelled) return;
        setState("open");
      };
      src.addEventListener("stats", (ev) => {
        if (cancelled) return;
        const msg = ev as MessageEvent;
        try {
          const parsed = JSON.parse(msg.data) as ContainersResponse;
          setData(parsed);
          setTickCount((n) => n + 1);
        } catch {
          // Malformed frame — log silently and keep the stream open.
        }
      });
      src.addEventListener("error", (ev) => {
        if (cancelled) return;
        const msg = ev as MessageEvent | Event;
        if ("data" in msg) {
          try {
            const parsed = JSON.parse((msg as MessageEvent).data) as { reason?: string };
            if (parsed.reason) setError(parsed.reason);
          } catch {
            // ignore
          }
        }
        // Browser will auto-reconnect when this is a transport
        // error; treat user-visible state as "transient error" so
        // the UI can show a small banner if it wants.
        setState("error");
      });
      src.onerror = () => {
        if (cancelled) return;
        // EventSource recovers on its own via the browser's
        // built-in retry logic. We only flip to "error" so the
        // UI can show a connection indicator; we never `close()`
        // here, otherwise auto-reconnect won't fire.
        setState("error");
      };
    };

    const sync = () => {
      if (typeof document === "undefined") {
        open();
        return;
      }
      if (document.visibilityState === "hidden") {
        close();
        setState("paused");
      } else {
        open();
      }
    };

    sync();
    document.addEventListener("visibilitychange", sync);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", sync);
      close();
      setState("idle");
    };
  }, []);

  return { data, state, error, tickCount };
}
