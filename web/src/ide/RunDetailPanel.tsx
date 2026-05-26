import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Box,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Loader2,
  Route,
  RotateCcw,
  Square,
  XCircle,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type RunDetail,
  type StackRerouteBody,
  type StackStatus as StackStatusType,
  getDeployRunDetail,
  getStackStatus,
  restartStack,
  rerouteStack,
  stopStack,
} from "@/api/environments";
import { useI18n } from "@/app/providers/i18n-context";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

interface Props {
  runId: string;
}

const STATUS_TONE: Record<string, "neutral" | "success" | "warn" | "danger" | "accent"> = {
  success: "success",
  failed: "danger",
  running: "accent",
  pending: "accent",
  aborted: "warn",
  rolled_back: "warn",
};

/** Right-pane view for a *past* deploy run. Loads from the DB-
 * backed `/api/deploy/runs/{id}/detail` endpoint (the in-memory
 * registry only retains live + recently-terminal handles for ~10
 * min). Shows the run header, the deploy target config used, the
 * full captured log_tail, and the bound URL.
 *
 * For *active* runs the parent uses the live SSE stream instead. */
export function RunDetailPanel({ runId }: Props) {
  const { t } = useI18n();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    void (async () => {
      try {
        const d = await getDeployRunDetail(runId);
        if (cancelled) return;
        setDetail(d);
      } catch (e) {
        if (cancelled) return;
        setErr(e instanceof ApiError ? e.reason : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-fg-muted">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="text-[12px]">{t("app.loading")}</span>
      </div>
    );
  }
  if (err) {
    return (
      <p
        role="alert"
        className="m-4 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
      >
        {err}
      </p>
    );
  }
  if (!detail) return null;

  const { run, environment, project } = detail;
  const StatusIcon = run.status === "success" ? CheckCircle2 : run.status === "failed" ? XCircle : AlertTriangle;
  const target = environment.deploy_target_config;
  const composePaths =
    Array.isArray(target.compose_paths) && target.compose_paths.length > 0
      ? (target.compose_paths as unknown[]).map(String)
      : target.compose_path
        ? [String(target.compose_path)]
        : [];
  const primaryService =
    typeof target.primary_service === "string" ? target.primary_service : null;
  const primaryPort =
    typeof target.primary_port === "number" ? target.primary_port : null;
  const previewMode =
    typeof target.preview_mode === "string" ? target.preview_mode : null;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-bg">
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-bg-elevated px-4 py-2.5">
        <StatusIcon
          className={cn(
            "h-4 w-4",
            run.status === "success"
              ? "text-success"
              : run.status === "failed"
                ? "text-danger"
                : "text-warn",
          )}
        />
        <span className="text-[13px] font-semibold text-fg">
          {project.display_name} · {environment.name}
        </span>
        <Badge tone={STATUS_TONE[run.status] ?? "neutral"}>{run.status}</Badge>
        <code className="font-mono text-[11px] text-fg-muted">{run.version}</code>
        <span className="text-[11px] text-fg-subtle">
          {run.trigger_kind} ·{" "}
          {run.finished_at
            ? new Date(run.finished_at).toLocaleString()
            : new Date(run.started_at).toLocaleString()}
        </span>
        {run.bound_url ? (
          <a
            href={run.bound_url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-success/40 bg-success/10 px-2.5 py-1 text-[11.5px] font-medium text-success hover:bg-success/20"
          >
            <ExternalLink className="h-3 w-3" />
            {run.bound_url}
          </a>
        ) : null}
      </header>

      <div className="grid min-h-0 flex-1 grid-rows-[auto_auto_1fr] overflow-hidden">
        {/* Config + meta strip */}
        <section className="grid shrink-0 grid-cols-1 gap-2 border-b border-border bg-bg-subtle/30 px-4 py-3 md:grid-cols-3">
          <Card title={t("deploy.detail.run")}>
            <KV k="run_id" v={run.id} mono />
            <KV k="exec_code" v={run.exec_code || "—"} mono />
            <KV k="actor" v={run.actor_id || "—"} />
            <KV
              k="started_at"
              v={new Date(run.started_at).toLocaleString()}
            />
            <KV
              k="finished_at"
              v={run.finished_at ? new Date(run.finished_at).toLocaleString() : "—"}
            />
          </Card>
          <Card title={t("deploy.detail.target")}>
            <KV k="kind" v={environment.deploy_target_kind} />
            <KV k="require_2fa" v={environment.require_2fa ? "yes" : "no"} />
            <KV
              k="cost_multiplier"
              v={environment.cost_multiplier.toString()}
            />
            <KV
              k="secret_refs"
              v={environment.secret_refs.length ? environment.secret_refs.join(", ") : "—"}
            />
            {previewMode ? <KV k="preview_mode" v={previewMode} /> : null}
          </Card>
          <Card title={t("deploy.detail.compose")}>
            {composePaths.length === 0 ? (
              <span className="text-[11px] text-fg-subtle">{t("deploy.detail.no_compose")}</span>
            ) : (
              <ul className="space-y-0.5">
                {composePaths.map((p) => (
                  <li key={p} className="flex items-center gap-1.5 text-[11.5px]">
                    <Box className="h-3 w-3 shrink-0 text-fg-subtle" strokeWidth={1.5} />
                    <code className="truncate font-mono text-fg" title={p}>
                      {p}
                    </code>
                  </li>
                ))}
              </ul>
            )}
            {primaryService ? (
              <div className="mt-1.5 text-[11px] text-fg-muted">
                {t("deploy.detail.primary")}: <code className="text-fg">{primaryService}</code>
                {primaryPort ? <code className="text-fg-subtle"> :{primaryPort}</code> : null}
              </div>
            ) : null}
          </Card>
        </section>

        {/* Stack management — only for local-compose deploys */}
        {environment.deploy_target_kind === "local" ? (
          <StackSection
            environmentId={environment.id}
            envName={environment.name}
            isSuccessRun={run.status === "success"}
            targetConfig={target}
            onConfigChange={(updated) => setDetail({
              ...detail,
              environment: { ...environment, deploy_target_config: updated },
            })}
          />
        ) : (
          <div />
        )}

        {/* Log */}
        <section className="flex min-h-0 flex-col overflow-hidden">
          <header className="flex shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-4 py-1.5 text-[11px] uppercase tracking-wider text-fg-muted">
            {t("deploy.detail.log")}
            <span className="text-[10px] text-fg-subtle">
              {t("deploy.detail.log_hint")}
            </span>
          </header>
          <pre className="flex-1 overflow-auto whitespace-pre-wrap break-all bg-bg px-4 py-3 font-mono text-[11px] leading-relaxed text-fg-muted">
            {run.log_tail || (
              <span className="text-fg-subtle">{t("deploy.detail.log_empty")}</span>
            )}
          </pre>
        </section>
      </div>
    </div>
  );
}

function StackSection({
  environmentId,
  envName,
  isSuccessRun,
  targetConfig,
  onConfigChange,
}: {
  environmentId: string;
  envName: string;
  isSuccessRun: boolean;
  targetConfig: Record<string, unknown>;
  onConfigChange: (updated: Record<string, unknown>) => void;
}) {
  const { t } = useI18n();
  const [status, setStatus] = useState<StackStatusType | null>(null);
  const [busy, setBusy] = useState<"down" | "restart" | "reroute" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rerouteOutput, setRerouteOutput] = useState<string | null>(null);
  const [showOverrides, setShowOverrides] = useState(false);

  // Form fields seeded from the saved target_config. Empty string
  // means "don't override this field" so the backend keeps using the
  // existing saved value. Booleans use a tri-state encoded as ""
  // (inherit), "true", "false".
  const cfg = targetConfig;
  const savedService = typeof cfg.primary_service === "string" ? cfg.primary_service : "";
  const savedPort = typeof cfg.primary_port === "number" ? String(cfg.primary_port) : "";
  const savedStrip =
    typeof cfg.strip_prefix === "boolean" ? (cfg.strip_prefix ? "true" : "false") : "";
  const savedScheme =
    cfg.upstream_scheme === "https" || cfg.upstream_scheme === "http"
      ? cfg.upstream_scheme
      : "";
  const savedHostHdr =
    typeof cfg.upstream_host_header === "string" ? cfg.upstream_host_header : "";
  const savedTlsSkip =
    typeof cfg.upstream_tls_insecure === "boolean"
      ? cfg.upstream_tls_insecure
        ? "true"
        : "false"
      : "";
  const savedMode =
    cfg.preview_mode === "subdomain" || cfg.preview_mode === "path"
      ? cfg.preview_mode
      : "";

  const [fService, setFService] = useState(savedService);
  const [fPort, setFPort] = useState(savedPort);
  const [fStrip, setFStrip] = useState(savedStrip);
  const [fScheme, setFScheme] = useState(savedScheme);
  const [fHostHdr, setFHostHdr] = useState(savedHostHdr);
  const [fTlsSkip, setFTlsSkip] = useState(savedTlsSkip);
  const [fMode, setFMode] = useState<string>(savedMode);

  const refresh = useCallback(async () => {
    try {
      const s = await getStackStatus(environmentId);
      setStatus(s);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : String(e));
    }
  }, [environmentId]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 4000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const onDown = async () => {
    if (
      !window.confirm(
        t("deploy.stack.confirm.down").replace("{name}", envName),
      )
    )
      return;
    setBusy("down");
    try {
      const r = await stopStack(environmentId);
      if (!r.ok) {
        window.alert(t("deploy.stack.failed.down") + "\n\n" + r.output.slice(-400));
      }
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : String(e));
    } finally {
      setBusy(null);
    }
  };

  const buildRerouteBody = (): StackRerouteBody => {
    // Only include fields the user has actually changed from the
    // saved values — otherwise we'd churn target_config with
    // identical writes and the success message wouldn't reflect what
    // the operator intentionally adjusted.
    const body: StackRerouteBody = {};
    if (fService !== savedService) body.primary_service = fService || null;
    if (fPort !== savedPort) {
      const n = Number.parseInt(fPort, 10);
      body.primary_port = Number.isFinite(n) && n > 0 ? n : null;
    }
    if (fStrip !== savedStrip) {
      body.strip_prefix = fStrip === "true" ? true : fStrip === "false" ? false : null;
    }
    if (fScheme !== savedScheme) {
      body.upstream_scheme = fScheme === "https" || fScheme === "http" ? fScheme : null;
    }
    if (fHostHdr !== savedHostHdr) body.upstream_host_header = fHostHdr;
    if (fTlsSkip !== savedTlsSkip) {
      body.upstream_tls_insecure =
        fTlsSkip === "true" ? true : fTlsSkip === "false" ? false : null;
    }
    if (fMode !== savedMode) {
      body.preview_mode =
        fMode === "subdomain" || fMode === "path" ? fMode : null;
    }
    return body;
  };

  const onReroute = async () => {
    const body = buildRerouteBody();
    const hasOverrides = Object.keys(body).length > 0;
    if (
      !window.confirm(
        (hasOverrides
          ? t("deploy.stack.confirm.reroute_overrides")
          : t("deploy.stack.confirm.reroute")
        ).replace("{name}", envName),
      )
    )
      return;
    setBusy("reroute");
    setRerouteOutput(null);
    try {
      const r = await rerouteStack(environmentId, hasOverrides ? body : undefined);
      setRerouteOutput(r.output);
      if (!r.ok) {
        window.alert(t("deploy.stack.failed.reroute") + "\n\n" + r.output.slice(-400));
      }
      // Optimistic config update — server already persisted, and the
      // detail panel won't refetch unless the user re-opens it.
      if (hasOverrides && r.ok) {
        const next = { ...targetConfig };
        if (body.primary_service !== undefined)
          next.primary_service = body.primary_service ?? undefined;
        if (body.primary_port !== undefined)
          next.primary_port = body.primary_port ?? undefined;
        if (body.strip_prefix !== undefined)
          next.strip_prefix = body.strip_prefix ?? undefined;
        if (body.upstream_scheme !== undefined)
          next.upstream_scheme = body.upstream_scheme ?? undefined;
        if (body.upstream_host_header !== undefined)
          next.upstream_host_header = body.upstream_host_header || undefined;
        if (body.upstream_tls_insecure !== undefined)
          next.upstream_tls_insecure = body.upstream_tls_insecure ?? undefined;
        if (body.preview_mode !== undefined)
          next.preview_mode = body.preview_mode ?? undefined;
        onConfigChange(next);
      }
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : String(e));
    } finally {
      setBusy(null);
    }
  };

  const onRestart = async () => {
    if (
      !window.confirm(
        t("deploy.stack.confirm.restart").replace("{name}", envName),
      )
    )
      return;
    setBusy("restart");
    try {
      const r = await restartStack(environmentId);
      if (!r.ok) {
        window.alert(t("deploy.stack.failed.restart") + "\n\n" + r.output.slice(-400));
      }
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : String(e));
    } finally {
      setBusy(null);
    }
  };

  const hasContainers = (status?.total_count ?? 0) > 0;

  return (
    <section className="border-b border-border bg-bg-subtle/20 px-4 py-3">
      <header className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
          {t("deploy.stack.title")}
        </h3>
        <span className="text-[10.5px] text-fg-subtle">
          {status ? `${status.running_count} / ${status.total_count} running` : "—"}
        </span>
        <code className="text-[10.5px] text-fg-subtle">{status?.project ?? "—"}</code>
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={onReroute}
            disabled={!hasContainers || busy !== null}
            title={t("deploy.stack.reroute.title")}
          >
            {busy === "reroute" ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <Route className="mr-1 h-3 w-3" />
            )}
            {t("deploy.stack.reroute")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onRestart}
            disabled={!hasContainers || busy !== null}
            title={t("deploy.stack.restart")}
          >
            {busy === "restart" ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <RotateCcw className="mr-1 h-3 w-3" />
            )}
            {t("deploy.stack.restart")}
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={onDown}
            disabled={!hasContainers || busy !== null}
            title={t("deploy.stack.down")}
          >
            {busy === "down" ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <Square className="mr-1 h-3 w-3" />
            )}
            {t("deploy.stack.down")}
          </Button>
        </div>
      </header>
      {err ? (
        <p
          role="alert"
          className="mb-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-1.5 text-[11px] text-danger"
        >
          {err}
        </p>
      ) : null}
      {rerouteOutput ? (
        <pre className="mb-2 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-md border border-success/40 bg-success/10 px-3 py-1.5 font-mono text-[11px] text-success">
          {rerouteOutput}
        </pre>
      ) : null}
      <div className="mb-2 rounded-md border border-border bg-bg-elevated">
        <button
          type="button"
          className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider text-fg-muted hover:bg-bg-subtle"
          onClick={() => setShowOverrides((v) => !v)}
          aria-expanded={showOverrides}
        >
          {showOverrides ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
          {t("deploy.stack.overrides.title")}
        </button>
        {showOverrides ? (
          <div className="grid grid-cols-1 gap-2 border-t border-border p-2.5 md:grid-cols-2 lg:grid-cols-3">
            <Field label={t("deploy.stack.overrides.preview_mode")}>
              <select
                className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-[11px] text-fg"
                value={fMode}
                onChange={(e) => setFMode(e.target.value)}
              >
                <option value="">— inherit —</option>
                <option value="path">path (apex/preview/&lt;slug&gt;)</option>
                <option value="subdomain">subdomain (&lt;slug&gt;.preview-domain)</option>
              </select>
            </Field>
            <Field label={t("deploy.stack.overrides.primary_service")}>
              <input
                className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-[11px] text-fg"
                value={fService}
                placeholder="nginx"
                onChange={(e) => setFService(e.target.value.trim())}
                spellCheck={false}
              />
            </Field>
            <Field label={t("deploy.stack.overrides.primary_port")}>
              <input
                className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-[11px] text-fg"
                value={fPort}
                placeholder="3000 / 80 / 443"
                onChange={(e) => setFPort(e.target.value.trim())}
                inputMode="numeric"
                spellCheck={false}
              />
            </Field>
            <Field label={t("deploy.stack.overrides.scheme")}>
              <select
                className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-[11px] text-fg"
                value={fScheme}
                onChange={(e) => setFScheme(e.target.value)}
              >
                <option value="">— inherit —</option>
                <option value="http">http</option>
                <option value="https">https</option>
              </select>
            </Field>
            <Field label={t("deploy.stack.overrides.host_header")}>
              <input
                className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-[11px] text-fg"
                value={fHostHdr}
                placeholder="example.com"
                onChange={(e) => setFHostHdr(e.target.value.trim())}
                spellCheck={false}
              />
            </Field>
            <Field label={t("deploy.stack.overrides.tls_insecure")}>
              <select
                className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-[11px] text-fg"
                value={fTlsSkip}
                onChange={(e) => setFTlsSkip(e.target.value)}
              >
                <option value="">— inherit —</option>
                <option value="false">verify</option>
                <option value="true">skip verify</option>
              </select>
            </Field>
            <Field label={t("deploy.stack.overrides.strip_prefix")}>
              <select
                className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-[11px] text-fg"
                value={fStrip}
                onChange={(e) => setFStrip(e.target.value)}
              >
                <option value="">— inherit —</option>
                <option value="true">true (strip /preview/&lt;slug&gt;)</option>
                <option value="false">false (keep prefix)</option>
              </select>
            </Field>
            <p className="md:col-span-2 lg:col-span-3 text-[10.5px] leading-snug text-fg-subtle">
              {t("deploy.stack.overrides.hint")}
            </p>
          </div>
        ) : null}
      </div>
      {!hasContainers ? (
        <p className="text-[11px] text-fg-subtle">
          {isSuccessRun
            ? t("deploy.stack.empty_after_success")
            : t("deploy.stack.empty")}
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-3">
          {status!.services.map((svc) => (
            <li
              key={svc.container_id}
              className="flex items-center gap-2 rounded border border-border bg-bg-elevated px-2 py-1 text-[11px]"
            >
              <StackStateDot status={svc.status} health={svc.health} />
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-fg" title={svc.container_name}>
                  {svc.service || svc.container_name}
                </div>
                <div className="truncate text-[10px] text-fg-subtle" title={svc.image}>
                  {svc.image}
                </div>
              </div>
              <Badge
                tone={
                  svc.status === "running"
                    ? svc.health && svc.health !== "healthy"
                      ? "warn"
                      : "success"
                    : svc.status === "exited" || svc.status === "dead"
                      ? "danger"
                      : "neutral"
                }
                className="text-[10px]"
              >
                {svc.status}
                {svc.health ? ` · ${svc.health}` : ""}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function StackStateDot({
  status,
  health,
}: {
  status: string;
  health: string | null;
}) {
  const color =
    status === "running"
      ? health && health !== "healthy"
        ? "bg-warn"
        : "bg-success"
      : status === "exited" || status === "dead"
        ? "bg-danger"
        : status === "paused"
          ? "bg-warn"
          : "bg-fg-subtle";
  return <span className={cn("inline-block h-1.5 w-1.5 shrink-0 rounded-full", color)} />;
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-bg-elevated p-2.5">
      <h3 className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-fg-muted">
        {title}
      </h3>
      <div>{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wider text-fg-subtle">{label}</span>
      {children}
    </label>
  );
}

function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-b border-border/40 py-0.5 last:border-b-0">
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-fg-subtle">
        {k}
      </span>
      <span
        className={cn(
          "truncate text-right text-[11.5px] text-fg",
          mono && "font-mono",
        )}
        title={v}
      >
        {v}
      </span>
    </div>
  );
}
