import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Pause, Play, RefreshCw, X } from "lucide-react";

import { fetchContainerLogs } from "@/api/performance";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

interface Props {
  containerId: string;
  containerName: string;
  onClose: () => void;
}

const POLL_MS = 2500;
const TAIL_LINES = 500;

/** Modal-style log tail viewer. Polls `/logs?tail=N` every 2.5s
 * while live; user can pause (so they can scroll back through
 * frozen output) and manual-refresh. Always-fresh "live" mode is
 * the default. */
export function LogsModal({ containerId, containerName, onClose }: Props) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const preRef = useRef<HTMLPreElement | null>(null);

  const pull = useCallback(async () => {
    try {
      const r = await fetchContainerLogs(containerId, TAIL_LINES);
      setText(r.text);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [containerId]);

  useEffect(() => {
    void pull();
  }, [pull]);

  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => void pull(), POLL_MS);
    return () => window.clearInterval(id);
  }, [live, pull]);

  // Auto-scroll to bottom on text change *only if the user is near
  // the bottom already* — so manual scroll-up to inspect older
  // lines isn't yanked back by the next poll.
  useEffect(() => {
    const el = preRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom < 80) {
      el.scrollTop = el.scrollHeight;
    }
  }, [text]);

  // ESC closes
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={(e) => {
        // Click outside the panel closes
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex h-[80vh] w-[min(1100px,92vw)] flex-col overflow-hidden rounded-lg border border-border bg-bg-elevated shadow-xl">
        <header className="flex items-center gap-3 border-b border-border px-4 py-2.5">
          <h2 className="text-[13px] font-semibold text-fg">Logs</h2>
          <code className="truncate font-mono text-[11.5px] text-fg-muted">
            {containerName}
          </code>
          <span className="text-[11px] text-fg-subtle">
            tail {TAIL_LINES} lines · auto-refresh {Math.round(POLL_MS / 1000)}s
          </span>
          <div className="ml-auto flex items-center gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setLive((l) => !l)}
              title={live ? "Pause auto-refresh" : "Resume auto-refresh"}
            >
              {live ? (
                <>
                  <Pause className="mr-1 h-3.5 w-3.5" />
                  Pause
                </>
              ) : (
                <>
                  <Play className="mr-1 h-3.5 w-3.5" />
                  Live
                </>
              )}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void pull()}
              title="Refresh now"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose} title="Close (ESC)">
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-hidden bg-bg">
          {loading && !text ? (
            <div className="flex h-full items-center justify-center text-fg-muted">
              <Loader2 className="h-4 w-4 animate-spin" />
            </div>
          ) : err ? (
            <p
              role="alert"
              className="m-3 rounded-md border border-danger/40 bg-danger/10 p-3 text-[12px] text-danger"
            >
              {err}
            </p>
          ) : (
            <pre
              ref={preRef}
              className={cn(
                "h-full overflow-auto whitespace-pre-wrap break-all bg-bg px-3 py-2 font-mono text-[11.5px] leading-snug text-fg-muted",
              )}
            >
              {text || (
                <span className="text-fg-subtle">No log output yet.</span>
              )}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
