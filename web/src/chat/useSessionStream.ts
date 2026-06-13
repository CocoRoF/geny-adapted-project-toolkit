import { useCallback, useEffect, useRef, useState } from "react";

import { type MessageReplayEntry, type SessionEventKind, streamUrl } from "@/api/sessions";

/** Live SSE consumer for `GET /api/sessions/{sid}/stream`.
 *
 * Browser `EventSource` doesn't send cookies cross-origin by default
 * but our SSE endpoint sits on the same origin (or behind the Vite
 * proxy in dev), so `withCredentials: true` is enough to forward the
 * session cookie. The hook auto-reconnects with `?since=<lastSeq>` on
 * unexpected disconnects.
 *
 * Returns `{ events, status, lastSeq, reset }`. `events` is the live
 * + replayed list in seq order. `status` is one of:
 *   `idle` (no session bound), `connecting`, `open`, `closed`,
 *   `error`. `reset()` clears `events` (e.g. when the parent flips
 *   to a new session). */

export type SessionStreamEvent = MessageReplayEntry;

export type StreamStatus = "idle" | "connecting" | "open" | "closed" | "error";

interface UseSessionStreamReturn {
  events: SessionStreamEvent[];
  status: StreamStatus;
  lastSeq: number;
  errorReason: string | null;
  reset: () => void;
}

export function useSessionStream(sessionId: string | null): UseSessionStreamReturn {
  const [events, setEvents] = useState<SessionStreamEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [errorReason, setErrorReason] = useState<string | null>(null);
  const lastSeqRef = useRef(0);
  const sourceRef = useRef<EventSource | null>(null);
  // Positive seqs already committed to `events`. Native EventSource
  // auto-reconnects on any transient drop, and the server replays the
  // turn's history from the top on a fresh connection — without this
  // guard every reconnect re-appended the WHOLE transcript, doubling
  // it. seqs are server-assigned + monotonic, so a seen seq is always
  // a true duplicate. (Non-finite seqs are rare malformed frames; we
  // never dedup those so the user still sees them.)
  const seenSeqsRef = useRef<Set<number>>(new Set());
  // When did we last see a terminal `done`/`error` frame?  The server
  // closes the SSE socket cleanly after a terminal frame; the browser
  // fires `onerror` for that close and we'd otherwise show a noisy
  // "Stream interrupted — attempting to reconnect." banner *even
  // though the session ended successfully*. We treat any onerror
  // within this grace window as the expected close, not an interruption.
  const lastTerminalAtRef = useRef(0);
  const TERMINAL_GRACE_MS = 3000;

  const reset = useCallback(() => {
    setEvents([]);
    lastSeqRef.current = 0;
    seenSeqsRef.current = new Set();
    setErrorReason(null);
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setStatus("idle");
      reset();
      sourceRef.current?.close();
      sourceRef.current = null;
      return;
    }

    // Phase L follow-up — every time `sessionId` changes (operator
    // switched sessions via the picker, or this is the first mount)
    // we wipe local state. Without the reset, lastSeqRef carried the
    // tail of the *previous* session into the new `/stream?since=…`
    // request, which the server interpreted as "skip the first N
    // events of the new session" and the chat opened blank.
    setEvents([]);
    lastSeqRef.current = 0;
    seenSeqsRef.current = new Set();
    lastTerminalAtRef.current = 0;
    setErrorReason(null);

    setStatus("connecting");
    const url = streamUrl(sessionId, undefined);
    const source = new EventSource(url, { withCredentials: true });
    sourceRef.current = source;

    function attach(kind: SessionEventKind) {
      source.addEventListener(kind, (event: MessageEvent<string>) => {
        const seq = Number(event.lastEventId);
        let data: Record<string, unknown> = {};
        try {
          data = JSON.parse(event.data) as Record<string, unknown>;
        } catch {
          // Malformed frame — surface it as a raw text event so the
          // user still sees something instead of a silent drop.
          data = { raw: event.data };
        }
        if (Number.isFinite(seq)) {
          lastSeqRef.current = Math.max(lastSeqRef.current, seq);
          // Drop a frame we've already committed (reconnect replay).
          if (seenSeqsRef.current.has(seq)) return;
          seenSeqsRef.current.add(seq);
        }
        if (kind === "done" || kind === "error") {
          lastTerminalAtRef.current = Date.now();
        }
        setEvents((prev) => [
          ...prev,
          {
            seq: Number.isFinite(seq) ? seq : prev.length + 1,
            kind,
            data,
            ts: new Date().toISOString(),
          },
        ]);
      });
    }

    for (const kind of [
      "text",
      "tool_call",
      "tool_result",
      "cost",
      "error",
      "done",
      "step",
      // Phase I.2 — user_message frames carry the user's prompt for
      // each turn so the chat replay has both sides. Missing from
      // this list = the right-aligned user bubble never appeared on
      // a fresh tab / replay path.
      "user_message",
    ] as const) {
      attach(kind);
    }

    source.onopen = () => {
      setStatus("open");
      setErrorReason(null);
    };
    source.onerror = () => {
      // EventSource transitions to readyState=2 (CLOSED) for terminal
      // errors and readyState=0 (CONNECTING) when it's about to retry.
      // We only ever paint the noisy "Stream interrupted" banner for
      // *unexpected* drops — after a `done`/`error` frame the server
      // closes the socket on purpose and the browser fires onerror
      // as part of that close. The 3 s grace window keeps the
      // post-success UI clean.
      const sinceTerminalMs = Date.now() - lastTerminalAtRef.current;
      const expectedClose = sinceTerminalMs < TERMINAL_GRACE_MS;
      if (source.readyState === EventSource.CLOSED || expectedClose) {
        setStatus("closed");
        setErrorReason(null);
      } else {
        setStatus("error");
        setErrorReason("Stream interrupted — attempting to reconnect.");
      }
    };

    return () => {
      source.close();
      sourceRef.current = null;
    };
  }, [sessionId, reset]);

  return {
    events,
    status,
    lastSeq: lastSeqRef.current,
    errorReason,
    reset,
  };
}
