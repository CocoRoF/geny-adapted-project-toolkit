import { useCallback, useEffect, useRef, useState } from "react";
import {
  Clipboard,
  ExternalLink,
  Eye,
  Globe,
  Loader2,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Square,
  Trash2,
  X,
} from "lucide-react";

import { ApiError } from "@/api/client";
import { parseJsonObject } from "@/lib/json";
import {
  deleteService,
  exposeService,
  listServices,
  restartService,
  startService,
  stopService,
  unexposeService,
  type ServiceState,
  type WorkspaceService,
} from "@/api/services";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";
import { Field, Input } from "@/ui/Input";

interface Props {
  workspaceId: string;
  /** Phase N.3 — when the panel is mounted inside the IDE SidePanel,
   *  clicking the globe on an exposed service opens a Preview tab in
   *  the editor column. Optional: when undefined the globe button is
   *  hidden (panel still works for service start / stop / expose). */
  onOpenPreview?: (url: string, label: string) => void;
}

const POLL_MS = 2000;

/** Panel for managing background dev servers + exposing their ports
 * via Caddy (or localhost fallback in dev). One row per service:
 * label / cmd / state badge / port / quick actions (logs · stop ·
 * restart · expose · delete). Plus an inline "+ New service" form.
 *
 * Polls every 2 s — server-side state machine is push-only inside
 * the registry; this poll is the cheapest way to surface auto_port
 * + exit-code transitions without piling another SSE channel onto
 * the connection budget. */
export function ServicesPanel({ workspaceId, onOpenPreview }: Props) {
  const [services, setServices] = useState<WorkspaceService[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [tailLabel, setTailLabel] = useState<string | null>(null);

  // Monotonic sequence guard: the 2s poll and post-action refreshes
  // race — a slow GET issued before an unexpose can resolve AFTER the
  // post-action refresh and clobber fresh state with pre-action data
  // (bound_url flickers back). Only the latest issued request may
  // commit.
  const seqRef = useRef(0);
  const refresh = useCallback(async () => {
    const seq = ++seqRef.current;
    try {
      const rows = await listServices(workspaceId);
      if (seq !== seqRef.current) return;
      setServices(rows);
      setErr(null);
    } catch (e) {
      if (seq !== seqRef.current) return;
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <div className="flex h-full flex-col bg-bg">
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-3 text-[12px]">
        <Play className="h-3.5 w-3.5 text-fg-muted" />
        <span className="font-medium text-fg">Services</span>
        {services.length > 0 ? (
          <Badge tone="accent" className="text-[10px]">
            {services.length}
          </Badge>
        ) : null}
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setShowForm((s) => !s)}
            title="New service"
          >
            {showForm ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
          </Button>
        </div>
      </header>

      {err ? (
        <p className="mx-3 my-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-1.5 text-[11px] text-danger">
          {err}
        </p>
      ) : null}

      <div className="flex-1 overflow-y-auto p-2">
        {showForm ? (
          <NewServiceForm
            onSubmit={async (input) => {
              try {
                await startService(workspaceId, input);
                setShowForm(false);
                await refresh();
              } catch (e) {
                setErr(
                  e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e),
                );
              }
            }}
            onCancel={() => setShowForm(false)}
          />
        ) : null}

        {loading && services.length === 0 ? (
          <p className="px-2 py-3 text-[11px] text-fg-subtle">Loading…</p>
        ) : services.length === 0 ? (
          <p className="px-2 py-3 text-[11px] text-fg-subtle">
            No services yet. Click <strong>+</strong> to start a dev server (e.g.{" "}
            <code className="text-fg-muted">npm run dev</code>).
          </p>
        ) : (
          services.map((svc) => (
            <ServiceRow
              key={svc.label}
              service={svc}
              workspaceId={workspaceId}
              onChanged={refresh}
              tailing={tailLabel === svc.label}
              onToggleTail={() => setTailLabel((c) => (c === svc.label ? null : svc.label))}
              {...(onOpenPreview ? { onOpenPreview } : {})}
            />
          ))
        )}
      </div>
    </div>
  );
}

function NewServiceForm({
  onSubmit,
  onCancel,
}: {
  onSubmit: (input: { label: string; cmd: string; port: number | null }) => Promise<void>;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState("web");
  const [cmd, setCmd] = useState("");
  const [port, setPort] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!label.trim() || !cmd.trim()) return;
        void (async () => {
          setBusy(true);
          try {
            const p = port.trim() === "" ? null : Number(port);
            await onSubmit({
              label: label.trim(),
              cmd: cmd.trim(),
              port: Number.isFinite(p) && p !== null ? p : null,
            });
          } finally {
            setBusy(false);
          }
        })();
      }}
      className="mb-2 rounded-md border border-border bg-bg-elevated p-2.5"
    >
      <div className="grid grid-cols-2 gap-2">
        <Field label="Label" hint="Letters / digits / `-` / `_`.">
          <Input
            value={label}
            onChange={(e) => setLabel(e.currentTarget.value)}
            placeholder="web"
            disabled={busy}
          />
        </Field>
        <Field label="Port" hint="Optional — auto-detected from the service's log.">
          <Input
            type="number"
            min={1}
            max={65535}
            value={port}
            onChange={(e) => setPort(e.currentTarget.value)}
            placeholder="3000"
            disabled={busy}
          />
        </Field>
      </div>
      <Field label="Command" hint="Runs in the worktree root.">
        <Input
          value={cmd}
          onChange={(e) => setCmd(e.currentTarget.value)}
          placeholder="npm run dev"
          disabled={busy}
        />
      </Field>
      <div className="mt-2 flex justify-end gap-1.5">
        <Button variant="ghost" type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy || !label.trim() || !cmd.trim()}>
          {busy ? "Starting…" : "Start"}
        </Button>
      </div>
    </form>
  );
}

function ServiceRow({
  service,
  workspaceId,
  onChanged,
  tailing,
  onToggleTail,
  onOpenPreview,
}: {
  service: WorkspaceService;
  workspaceId: string;
  onChanged: () => Promise<void>;
  tailing: boolean;
  onToggleTail: () => void;
  onOpenPreview?: (url: string, label: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);

  // Returns a SYNC (void) handler so it sits on onClick directly;
  // the async work runs in a self-owned IIFE with full error capture
  // — a failed expose (e.g. Caddy admin down, or the service bound
  // localhost-only) surfaces as a per-row message instead of an
  // unhandled rejection with zero feedback.
  const run = (fn: () => Promise<unknown>) => () => {
    void (async () => {
      setBusy(true);
      setActionErr(null);
      try {
        await fn();
        await onChanged();
      } catch (e) {
        setActionErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    })();
  };

  const isAlive = service.state === "running" || service.state === "starting";
  // Live detection outranks the declared port: when a dev server
  // finds its declared port taken (vite: 5173→5174) only the
  // detected one is real.
  const effectivePort = service.auto_port ?? service.port;
  const portDrift =
    service.port !== null && service.auto_port !== null && service.port !== service.auto_port;

  return (
    <div className="@container mb-1.5 rounded-md border border-border bg-bg-elevated">
      <div className="flex items-center gap-2 px-2.5 py-1.5">
        <StateDot state={service.state} />
        <strong className="font-mono text-[12px] text-fg">{service.label}</strong>
        <span
          className="hidden min-w-0 flex-1 truncate font-mono text-[11px] text-fg-subtle @[280px]:inline"
          title={service.cmd}
        >
          {service.cmd}
        </span>
        <span className="min-w-0 flex-1 @[280px]:hidden" aria-hidden />
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {portDrift ? (
            <Badge
              tone="warn"
              className="text-[10px]"
              title={`선언 포트 :${service.port}가 사용 중이라 서버가 :${service.auto_port}로 옮겨갔어요. Expose는 감지된 포트를 사용합니다.`}
            >
              :{service.port}→:{service.auto_port}
            </Badge>
          ) : effectivePort ? (
            <Badge tone="neutral" className="text-[10px]">
              :{effectivePort}
            </Badge>
          ) : null}
          <Badge
            tone={stateTone(service.state)}
            className="hidden text-[10px] @[220px]:inline-flex"
            title={service.state}
          >
            {service.state}
          </Badge>
        </span>
      </div>

      {service.bound_url ? (
        <div className="flex items-center gap-1.5 border-t border-border bg-bg-subtle px-2.5 py-1">
          <Globe className="h-3 w-3 shrink-0 text-accent" />
          {onOpenPreview ? (
            // Phase N.3 — the URL text itself is the primary "open
            // inside IDE" action. Matches the user's mental model:
            // they see a prominent link and expect it to render inline
            // (VSCode Simple Browser parity). The small external-link
            // icon to the right is the secondary "kick out to browser"
            // escape hatch.
            <button
              type="button"
              onClick={() => {
                if (service.bound_url) {
                  onOpenPreview(service.bound_url, service.label);
                }
              }}
              title="Open in IDE preview tab"
              className="min-w-0 flex-1 truncate text-left text-[11px] text-accent hover:underline focus:outline-none focus:ring-2 focus:ring-accent"
            >
              {service.bound_url}
            </button>
          ) : (
            // Fallback for callers that don't wire onOpenPreview —
            // a plain external link.
            <a
              href={service.bound_url}
              target="_blank"
              rel="noopener noreferrer"
              className="min-w-0 flex-1 truncate text-[11px] text-accent hover:underline"
            >
              {service.bound_url}
            </a>
          )}
          <a
            href={service.bound_url}
            target="_blank"
            rel="noopener noreferrer"
            title="Open in new browser tab"
            className="grid h-6 w-6 place-items-center rounded text-fg-muted hover:bg-bg hover:text-fg"
          >
            <ExternalLink className="h-3 w-3" />
          </a>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => {
              if (navigator.clipboard && service.bound_url) {
                void navigator.clipboard.writeText(service.bound_url);
              }
            }}
            title="Copy URL"
          >
            <Clipboard className="h-3 w-3" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={run(() => unexposeService(workspaceId, service.label))}
            disabled={busy}
            title="Unexpose"
          >
            <X className="h-3 w-3 text-fg-muted" />
          </Button>
        </div>
      ) : null}

      <div className="flex items-center gap-1 border-t border-border px-2 py-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleTail}
          className="h-7 px-2 text-[11px]"
          title="Show / hide live log"
        >
          <Eye className={cn("h-3 w-3 @[260px]:mr-1", tailing && "text-accent")} />
          <span className="hidden @[260px]:inline">Logs</span>
        </Button>
        {isAlive ? (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={run(() =>
                exposeService(
                  workspaceId,
                  service.label,
                  effectivePort ? { port: effectivePort } : undefined,
                ),
              )}
              disabled={busy || (!service.port && !service.auto_port)}
              className="h-7 px-2 text-[11px]"
              title={
                effectivePort
                  ? `Bind via Caddy → preview URL (:${effectivePort})`
                  : "Waiting for the service to print its port"
              }
            >
              <Pencil className="h-3 w-3 @[260px]:mr-1" />
              <span className="hidden @[260px]:inline">Expose</span>
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={run(() => stopService(workspaceId, service.label))}
              disabled={busy}
              className="h-7 px-2 text-[11px]"
              title="Stop"
            >
              <Square className="h-3 w-3 @[260px]:mr-1" />
              <span className="hidden @[260px]:inline">Stop</span>
            </Button>
          </>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            onClick={run(() => restartService(workspaceId, service.label))}
            disabled={busy}
            className="h-7 px-2 text-[11px]"
            title="Restart"
          >
            <RotateCcw className="h-3 w-3 @[260px]:mr-1" />
            <span className="hidden @[260px]:inline">Restart</span>
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={run(() => deleteService(workspaceId, service.label))}
          disabled={busy}
          className="ml-auto h-7 px-2 text-[11px] text-danger hover:text-danger"
          title="Stop + remove"
        >
          <Trash2 className="h-3 w-3" />
        </Button>
      </div>

      {actionErr ? (
        <p className="border-t border-danger/30 bg-danger/10 px-2.5 py-1 text-[10.5px] text-danger">
          {actionErr}
        </p>
      ) : null}

      {tailing ? <ServiceLogTail workspaceId={workspaceId} logPath={service.log_path} /> : null}
    </div>
  );
}

function StateDot({ state }: { state: ServiceState }) {
  const color =
    state === "running"
      ? "bg-success"
      : state === "starting" || state === "stopping"
        ? "bg-accent animate-pulse"
        : state === "failed"
          ? "bg-danger"
          : "bg-fg-subtle";
  return <span className={cn("inline-block h-1.5 w-1.5 shrink-0 rounded-full", color)} />;
}

function stateTone(state: ServiceState): "neutral" | "success" | "warn" | "danger" | "accent" {
  if (state === "running") return "success";
  if (state === "failed") return "danger";
  if (state === "starting" || state === "stopping") return "accent";
  if (state === "exited") return "warn";
  return "neutral";
}

/** Tiny SSE log tail inline in the panel. Reconnects with the last
 * byte offset so a brief drop doesn't replay the entire history. */
function ServiceLogTail({ workspaceId, logPath }: { workspaceId: string; logPath: string }) {
  const [lines, setLines] = useState<string[]>([]);
  const offsetRef = useRef(0);
  const scrollRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    setLines([]);
    offsetRef.current = 0;
    const url = `/_gapt/api/workspaces/${workspaceId}/file-tail?path=${encodeURIComponent(logPath)}&since_byte=${offsetRef.current}`;
    const src = new EventSource(url, { withCredentials: true });
    src.onmessage = (ev) => {
      try {
        const data = parseJsonObject(ev.data);
        if (!data) return;
        const text = typeof data["text"] === "string" ? data["text"] : "";
        if (text) {
          setLines((prev) => prev.concat(text).slice(-1000));
        }
        const id = Number(ev.lastEventId);
        if (Number.isFinite(id)) offsetRef.current = id;
      } catch {
        // ignore
      }
    };
    return () => {
      src.close();
    };
  }, [workspaceId, logPath]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [lines]);

  return (
    <pre
      ref={scrollRef}
      className="m-0 max-h-[200px] overflow-auto border-t border-border bg-bg px-3 py-1.5 font-mono text-[10.5px] leading-snug text-fg-muted"
    >
      {lines.length === 0 ? (
        <span className="inline-flex items-center gap-1 text-fg-subtle">
          <Loader2 className="h-3 w-3 animate-spin" /> tailing…
        </span>
      ) : (
        lines.map((l, i) => (
          <span key={i} className="block whitespace-pre">
            {l}
          </span>
        ))
      )}
    </pre>
  );
}
