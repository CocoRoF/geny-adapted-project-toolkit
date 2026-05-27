import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Clipboard,
  ExternalLink,
  Eye,
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
export function ServicesPanel({ workspaceId }: Props) {
  const [services, setServices] = useState<WorkspaceService[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [tailLabel, setTailLabel] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const rows = await listServices(workspaceId);
      setServices(rows);
      setErr(null);
    } catch (e) {
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
      onSubmit={async (e) => {
        e.preventDefault();
        if (!label.trim() || !cmd.trim()) return;
        setBusy(true);
        try {
          const p = port.trim() === "" ? null : Number(port);
          await onSubmit({
            label: label.trim(),
            cmd: cmd.trim(),
            port: Number.isFinite(p) && p !== null ? (p as number) : null,
          });
        } finally {
          setBusy(false);
        }
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
}: {
  service: WorkspaceService;
  workspaceId: string;
  onChanged: () => Promise<void>;
  tailing: boolean;
  onToggleTail: () => void;
}) {
  const [busy, setBusy] = useState(false);

  const run = (fn: () => Promise<unknown>) => async () => {
    setBusy(true);
    try {
      await fn();
      await onChanged();
    } finally {
      setBusy(false);
    }
  };

  const isAlive = service.state === "running" || service.state === "starting";
  const effectivePort = service.port ?? service.auto_port;

  return (
    <div className="mb-1.5 rounded-md border border-border bg-bg-elevated">
      <div className="flex items-center gap-2 px-2.5 py-1.5">
        <StateDot state={service.state} />
        <strong className="font-mono text-[12px] text-fg">{service.label}</strong>
        <span className="truncate font-mono text-[11px] text-fg-subtle" title={service.cmd}>
          {service.cmd}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {effectivePort ? (
            <Badge tone="neutral" className="text-[10px]">
              :{effectivePort}
            </Badge>
          ) : null}
          <Badge tone={stateTone(service.state)} className="text-[10px]">
            {service.state}
          </Badge>
        </span>
      </div>

      {service.bound_url ? (
        <div className="flex items-center gap-2 border-t border-border bg-bg-subtle px-2.5 py-1">
          <ExternalLink className="h-3 w-3 shrink-0 text-accent" />
          <a
            href={service.bound_url}
            target="_blank"
            rel="noopener noreferrer"
            className="truncate text-[11px] text-accent hover:underline"
          >
            {service.bound_url}
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

      <div className="flex items-center gap-1.5 border-t border-border px-2 py-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleTail}
          className="h-7 px-2 text-[11px]"
          title="Show / hide live log"
        >
          <Eye className={cn("mr-1 h-3 w-3", tailing && "text-accent")} />
          Logs
        </Button>
        {isAlive ? (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={run(() => exposeService(workspaceId, service.label))}
              disabled={busy || (!service.port && !service.auto_port)}
              className="h-7 px-2 text-[11px]"
              title={
                effectivePort
                  ? "Bind via Caddy → preview URL"
                  : "Waiting for the service to print its port"
              }
            >
              <Pencil className="mr-1 h-3 w-3" /> Expose
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={run(() => stopService(workspaceId, service.label))}
              disabled={busy}
              className="h-7 px-2 text-[11px]"
              title="Stop"
            >
              <Square className="mr-1 h-3 w-3" /> Stop
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
            <RotateCcw className="mr-1 h-3 w-3" /> Restart
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
          <Trash2 className="mr-1 h-3 w-3" />
        </Button>
      </div>

      {tailing ? (
        <ServiceLogTail workspaceId={workspaceId} logPath={service.log_path} />
      ) : null}
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
function ServiceLogTail({
  workspaceId,
  logPath,
}: {
  workspaceId: string;
  logPath: string;
}) {
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
        const data = JSON.parse(ev.data) as Record<string, unknown>;
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
