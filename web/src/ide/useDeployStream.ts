import { useEffect, useRef, useState } from "react";

export type DeployStreamPhase = "idle" | "connecting" | "open" | "done" | "error";

export interface DeployStreamState {
  phase: DeployStreamPhase;
  status: string; // pending | running | success | failed | aborted
  log: string;
  boundUrl: string | null;
  execCode: string | null;
  finishedAt: string | null;
  error: string | null;
}

const INITIAL: DeployStreamState = {
  phase: "idle",
  status: "pending",
  log: "",
  boundUrl: null,
  execCode: null,
  finishedAt: null,
  error: null,
};

/** Subscribe to `/_gapt/api/deploy/runs/{runId}/stream` via SSE.
 *
 * - When `runId` is null, the hook is idle (no connection).
 * - When `runId` flips to a new id, the previous EventSource is
 *   closed and a fresh one opens. The server replays the captured
 *   log buffer first, so an arbitrary mount-time `runId` (e.g.
 *   discovered via `getActiveDeploy`) gets the full history.
 * - On `done` the connection closes itself — we don't auto-reconnect
 *   for terminal states.
 *
 * The hook is intentionally dumb about server lifecycle — the
 * server keeps the deploy task running regardless of the EventSource
 * being open. Tabs can come and go; this hook just attaches and
 * detaches. */
export function useDeployStream(runId: string | null): DeployStreamState {
  const [state, setState] = useState<DeployStreamState>(INITIAL);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // Tear down any prior connection on runId change / unmount.
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
    if (!runId) {
      setState(INITIAL);
      return;
    }
    setState({ ...INITIAL, phase: "connecting" });

    const src = new EventSource(`/_gapt/api/deploy/runs/${runId}/stream`, {
      withCredentials: true,
    });
    sourceRef.current = src;
    let closed = false;

    src.onopen = () => {
      if (closed) return;
      // Reset the accumulated log on every (re)connect. The deploy
      // stream replays its full log from the top on a fresh
      // connection, so without this an auto-reconnect after a transient
      // drop would append a second copy of everything. First connect
      // resets an already-empty string (no-op).
      setState((s) => ({ ...s, phase: "open", log: "" }));
    };

    src.addEventListener("status", (ev) => {
      if (closed) return;
      try {
        if (typeof ev.data !== "string") return;
        const data = JSON.parse(ev.data) as {
          status: string;
          bound_url?: string | null;
          exec_code?: string | null;
        };
        setState((s) => ({
          ...s,
          status: data.status ?? s.status,
          boundUrl: data.bound_url ?? s.boundUrl,
          execCode: data.exec_code ?? s.execCode,
        }));
      } catch {
        // Ignore malformed frames.
      }
    });

    src.addEventListener("log", (ev) => {
      if (closed) return;
      try {
        if (typeof ev.data !== "string") return;
        const data = JSON.parse(ev.data) as { content?: string };
        if (typeof data.content !== "string") return;
        setState((s) => ({ ...s, log: s.log + data.content! }));
      } catch {
        // ignore
      }
    });

    src.addEventListener("done", (ev) => {
      if (closed) return;
      try {
        if (typeof ev.data !== "string") return;
        const data = JSON.parse(ev.data) as {
          status: string;
          bound_url?: string | null;
          exec_code?: string | null;
          finished_at?: string | null;
        };
        setState((s) => ({
          ...s,
          phase: "done",
          status: data.status ?? s.status,
          boundUrl: data.bound_url ?? s.boundUrl,
          execCode: data.exec_code ?? s.execCode,
          finishedAt: data.finished_at ?? null,
        }));
      } catch {
        setState((s) => ({ ...s, phase: "done" }));
      }
      // Server closes the stream after `done`; we close our side
      // too so the browser doesn't auto-reconnect.
      closed = true;
      src.close();
      sourceRef.current = null;
    });

    src.onerror = () => {
      if (closed) return;
      // Transient network error → browser will auto-reconnect via
      // EventSource semantics. Flip phase so the UI can show a
      // small indicator. We don't tear down here.
      setState((s) => ({ ...s, phase: "error" }));
    };

    return () => {
      closed = true;
      src.close();
      sourceRef.current = null;
    };
  }, [runId]);

  return state;
}
