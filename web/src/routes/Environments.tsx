import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft, Plus, Rocket, ShieldAlert, Trash2 } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  createEnvironment,
  deleteEnvironment,
  type DeployTargetKind,
  type EnvironmentPayload,
  type EnvironmentResponse,
  listEnvironments,
  triggerDeploy,
  updateEnvironment,
  type DeployResultResponse,
} from "@/api/environments";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/Card";
import { ConfirmDialog } from "@/ui/ConfirmDialog";
import { Field, Input, Select, Textarea } from "@/ui/Input";
import { Modal } from "@/ui/Modal";

const TARGET_CHOICES: { value: DeployTargetKind; label: string }[] = [
  { value: "local", label: "Local Compose (docker compose up on this host)" },
  { value: "remote_ssh", label: "Remote SSH (ssh + docker compose on a VPS)" },
  { value: "webhook", label: "Webhook (HMAC-signed POST to a deploy endpoint)" },
  { value: "k8s", label: "Kubernetes (M4 — not yet implemented)" },
];

export function Environments() {
  const { pid } = useParams<{ pid: string }>();
  const projectId = pid ?? "";

  const [envs, setEnvs] = useState<EnvironmentResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<EnvironmentResponse | "new" | null>(null);
  const [deploying, setDeploying] = useState<EnvironmentResponse | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<EnvironmentResponse | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try {
      const rows = await listEnvironments(projectId);
      setEnvs(rows);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!projectId) return null;

  return (
    <div className="mx-auto max-w-[960px] px-6 py-8">
      <Link
        to={`/projects/${projectId}`}
        className="mb-3 inline-flex items-center gap-1 text-[12px] text-fg-muted hover:text-fg"
      >
        <ChevronLeft className="h-3.5 w-3.5" /> Back to project
      </Link>
      <header className="mb-6 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-bg-subtle">
            <Rocket className="h-4 w-4 text-fg-muted" />
          </div>
          <div>
            <h1 className="text-[20px] font-semibold tracking-tight text-fg">Environments</h1>
            <p className="text-[12px] text-fg-muted">
              Deploy targets for this project — pick which env you ship to, with policy
              gates per target.
            </p>
          </div>
        </div>
        <Button onClick={() => setEditing("new")}>
          <Plus className="mr-1.5 h-4 w-4" /> New environment
        </Button>
      </header>

      {err ? (
        <Card className="mb-4 border-danger/40">
          <CardContent className="p-3 text-[12px] text-danger">{err}</CardContent>
        </Card>
      ) : null}

      {loading ? (
        <Card>
          <CardContent className="p-4 text-[12px] text-fg-subtle">Loading…</CardContent>
        </Card>
      ) : envs.length === 0 ? (
        <Card>
          <CardContent className="p-6 text-center text-[13px] text-fg-muted">
            No environments yet. Click <strong>New environment</strong> to add one (e.g.
            <code className="mx-1 rounded bg-bg-subtle px-1.5 py-0.5 font-mono">staging</code>
            running a local docker-compose).
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {envs.map((env) => (
            <EnvironmentCard
              key={env.id}
              env={env}
              onDeploy={() => setDeploying(env)}
              onEdit={() => setEditing(env)}
              onDelete={() => setConfirmDelete(env)}
            />
          ))}
        </div>
      )}

      {editing ? (
        <EnvironmentEditorModal
          initial={editing === "new" ? null : editing}
          projectId={projectId}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      ) : null}

      {deploying ? (
        <DeployModal
          env={deploying}
          onClose={() => setDeploying(null)}
        />
      ) : null}

      {confirmDelete ? (
        <ConfirmDialog
          open
          title="Delete environment?"
          description={`Remove ${confirmDelete.name}? Future deploys to this target will require recreating it.`}
          confirmLabel="Delete"
          cancelLabel="Cancel"
          tone="danger"
          onCancel={() => setConfirmDelete(null)}
          onConfirm={async () => {
            try {
              await deleteEnvironment(confirmDelete.id);
              setConfirmDelete(null);
              await refresh();
            } catch (e) {
              setErr(
                e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e),
              );
            }
          }}
        />
      ) : null}
    </div>
  );
}

function EnvironmentCard({
  env,
  onDeploy,
  onEdit,
  onDelete,
}: {
  env: EnvironmentResponse;
  onDeploy: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle className="flex items-center gap-2">
            {env.name}
            <Badge tone="neutral" className="text-[10px]">
              {env.deploy_target_kind}
            </Badge>
            {env.require_2fa ? (
              <Badge tone="warn" className="gap-1 text-[10px]">
                <ShieldAlert className="h-3 w-3" /> 2FA
              </Badge>
            ) : null}
          </CardTitle>
          <CardDescription className="mt-1.5">
            <code className="font-mono text-[11px]">
              {summariseTarget(env.deploy_target_kind, env.deploy_target_config)}
            </code>
          </CardDescription>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button onClick={onDeploy}>
            <Rocket className="mr-1 h-4 w-4" /> Deploy
          </Button>
          <Button variant="ghost" onClick={onEdit}>
            Edit
          </Button>
          <Button variant="ghost" size="icon" onClick={onDelete} title="Delete">
            <Trash2 className="h-4 w-4 text-danger" />
          </Button>
        </div>
      </CardHeader>
    </Card>
  );
}

function summariseTarget(kind: DeployTargetKind, cfg: Record<string, unknown>): string {
  if (kind === "local") {
    return `compose: ${(cfg["compose_path"] as string) ?? "docker-compose.yml"}`;
  }
  if (kind === "remote_ssh") {
    return `${(cfg["host"] as string) ?? "?"}:${(cfg["compose_path"] as string) ?? "?"}`;
  }
  if (kind === "webhook") {
    return `POST ${(cfg["url"] as string) ?? "(unset)"}`;
  }
  return "(k8s — not implemented yet)";
}

// ────────────────────────────────────────── editor modal ──

interface EditorState {
  name: string;
  kind: DeployTargetKind;
  configText: string;
  require2fa: boolean;
  costMultiplier: string;
  // "TLS terminator nginx" assist — structured fields encoded into
  // the config JSON. When enabled, GAPT pre-fills the upstream-*
  // overrides that match the typical Cloudflare-origin-cert nginx
  // pattern so the user doesn't have to figure out from a failed
  // deploy that they need HTTPS + skip-verify + Host rewrite.
  tlsTerminator: boolean;
  tlsDomain: string;
  tlsService: string;
  tlsPort: string;
}

/** Heuristic: a config that already has scheme=https + tls_insecure
 * = true is almost certainly the TLS-terminator pattern. Surfacing
 * it via the structured fields on edit means the operator sees their
 * past choice as a checkbox + fields, not a JSON blob. */
function detectTlsTerminator(cfg: Record<string, unknown>): boolean {
  return cfg.upstream_scheme === "https" && cfg.upstream_tls_insecure === true;
}

function envToEditorState(env: EnvironmentResponse | null): EditorState {
  if (env === null) {
    return {
      name: "",
      kind: "local",
      configText: JSON.stringify({ compose_path: "docker-compose.yml" }, null, 2),
      require2fa: false,
      costMultiplier: "1.0",
      tlsTerminator: false,
      tlsDomain: "",
      tlsService: "nginx",
      tlsPort: "443",
    };
  }
  const cfg = env.deploy_target_config ?? {};
  const tlsOn = detectTlsTerminator(cfg);
  return {
    name: env.name,
    kind: env.deploy_target_kind,
    configText: JSON.stringify(cfg, null, 2),
    require2fa: env.require_2fa ?? false,
    costMultiplier: String(env.cost_multiplier ?? 1),
    tlsTerminator: tlsOn,
    tlsDomain: typeof cfg.upstream_host_header === "string" ? cfg.upstream_host_header : "",
    tlsService: typeof cfg.primary_service === "string" ? cfg.primary_service : "nginx",
    tlsPort: typeof cfg.primary_port === "number" ? String(cfg.primary_port) : "443",
  };
}

function EnvironmentEditorModal({
  initial,
  projectId,
  onClose,
  onSaved,
}: {
  initial: EnvironmentResponse | null;
  projectId: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [state, setState] = useState<EditorState>(() => envToEditorState(initial));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(state.configText || "{}");
    } catch (e) {
      setErr(`config must be valid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    if (typeof config !== "object" || config === null) {
      setErr("config must be a JSON object");
      return;
    }
    // Merge TLS-terminator structured fields back into the config
    // JSON. We OVERWRITE the matching keys (the structured fields
    // are the source of truth when the checkbox is on) so the user
    // can't accidentally leave stale JSON values. When the checkbox
    // is OFF we clear the upstream-* keys so a previously-tuned env
    // can be reverted to plain HTTP via the UI.
    if (state.kind === "local") {
      if (state.tlsTerminator) {
        const port = Number.parseInt(state.tlsPort, 10);
        config.upstream_scheme = "https";
        config.upstream_tls_insecure = true;
        config.upstream_host_header = state.tlsDomain.trim();
        config.primary_service = state.tlsService.trim() || "nginx";
        config.primary_port = Number.isFinite(port) && port > 0 ? port : 443;
        // strip_prefix=true is the right default for nginx that
        // doesn't know about /preview/<slug> — see the
        // StackRerouteHelpModal scenarios table.
        if (config.strip_prefix === undefined) {
          config.strip_prefix = true;
        }
      } else {
        // Toggle off: clean up the TLS-terminator keys. Keep
        // primary_service / primary_port as-is in case the user set
        // them deliberately for a non-TLS upstream.
        delete config.upstream_scheme;
        delete config.upstream_tls_insecure;
        delete config.upstream_host_header;
      }
    }
    const payload: EnvironmentPayload = {
      name: state.name.trim(),
      deploy_target_kind: state.kind,
      deploy_target_config: config,
      require_2fa: state.require2fa,
      cost_multiplier: Number(state.costMultiplier) || 1,
    };
    setBusy(true);
    setErr(null);
    try {
      if (initial) await updateEnvironment(initial.id, payload);
      else await createEnvironment(projectId, payload);
      await onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="md"
      title={initial ? `Edit environment · ${initial.name}` : "New environment"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || state.name.trim() === ""}>
            {busy ? "Saving…" : initial ? "Save" : "Create"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name" hint="Unique within the project.">
            <Input
              value={state.name}
              onChange={(e) => setState((s) => ({ ...s, name: e.target.value }))}
              placeholder="staging"
              disabled={busy}
            />
          </Field>
          <Field label="Target kind">
            <Select
              value={state.kind}
              onChange={(e) =>
                setState((s) => ({ ...s, kind: e.target.value as DeployTargetKind }))
              }
              disabled={busy}
            >
              {TARGET_CHOICES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {state.kind === "local" ? (
          <div className="rounded-md border border-border bg-bg-subtle/40 p-3">
            <label className="flex cursor-pointer items-start gap-2 text-[12.5px]">
              <input
                type="checkbox"
                className="mt-0.5 h-3.5 w-3.5"
                checked={state.tlsTerminator}
                onChange={(e) =>
                  setState((s) => ({ ...s, tlsTerminator: e.target.checked }))
                }
                disabled={busy}
              />
              <span>
                <span className="font-medium text-fg">
                  This stack ships its own TLS-terminator nginx (Cloudflare-origin-cert pattern)
                </span>
                <span className="block text-[11px] text-fg-muted">
                  Check this when your prod stack has an internal nginx that
                  forces HTTP→HTTPS and routes by <code>server_name</code>.
                  GAPT will dial it over HTTPS (skip-verify, since the
                  internal docker DNS name won't match the public cert) and
                  rewrite the Host header to the domain you specify.
                </span>
              </span>
            </label>
            {state.tlsTerminator ? (
              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                <Field
                  label="Public domain"
                  hint="nginx server_name (e.g. example.com)"
                >
                  <Input
                    value={state.tlsDomain}
                    onChange={(e) =>
                      setState((s) => ({ ...s, tlsDomain: e.target.value }))
                    }
                    placeholder="example.com"
                    disabled={busy}
                  />
                </Field>
                <Field
                  label="nginx service"
                  hint="compose service name"
                >
                  <Input
                    value={state.tlsService}
                    onChange={(e) =>
                      setState((s) => ({ ...s, tlsService: e.target.value }))
                    }
                    placeholder="nginx"
                    disabled={busy}
                  />
                </Field>
                <Field label="HTTPS port" hint="nginx container's TLS port">
                  <Input
                    value={state.tlsPort}
                    onChange={(e) =>
                      setState((s) => ({ ...s, tlsPort: e.target.value }))
                    }
                    placeholder="443"
                    inputMode="numeric"
                    disabled={busy}
                  />
                </Field>
              </div>
            ) : null}
            <p className="mt-2 text-[10.5px] text-fg-subtle">
              Not sure? Leave unchecked and deploy — GAPT auto-detects this
              pattern on the first probe and offers to apply it.
            </p>
          </div>
        ) : null}

        <Field
          label="Target config (JSON)"
          hint={configHintFor(state.kind)}
        >
          <Textarea
            value={state.configText}
            onChange={(e) => setState((s) => ({ ...s, configText: e.target.value }))}
            rows={10}
            className="font-mono"
            disabled={busy}
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Require 2FA" hint="Deploy refuses without a valid code.">
            <label className="flex h-8 items-center gap-2 rounded-md border border-border bg-surface px-2.5 text-[13px]">
              <input
                type="checkbox"
                checked={state.require2fa}
                onChange={(e) => setState((s) => ({ ...s, require2fa: e.target.checked }))}
                disabled={busy}
              />
              {state.require2fa ? "Required" : "Not required"}
            </label>
          </Field>
          <Field label="Cost multiplier" hint="Affects cost reporting; usually 1.">
            <Input
              type="number"
              step={0.1}
              min={0}
              value={state.costMultiplier}
              onChange={(e) => setState((s) => ({ ...s, costMultiplier: e.target.value }))}
              disabled={busy}
            />
          </Field>
        </div>

        {err ? (
          <p
            role="alert"
            className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
          >
            {err}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

function configHintFor(kind: DeployTargetKind): string {
  if (kind === "local") {
    return 'e.g. {"compose_path": "docker-compose.yml", "compose_paths": ["a.yml","b.yml"]}';
  }
  if (kind === "remote_ssh") {
    return 'e.g. {"host":"server","user":"deploy","port":22,"key_secret_ref":"ssh_key_id","compose_path":"/srv/app/docker-compose.yml"}';
  }
  if (kind === "webhook") {
    return 'e.g. {"url":"https://hook.example.com/deploy","secret_ref":"webhook_secret_id","env_keys":["API_URL"]}';
  }
  return "(k8s not yet supported)";
}

// ────────────────────────────────────────────── deploy modal ──

function DeployModal({
  env,
  onClose,
}: {
  env: EnvironmentResponse;
  onClose: () => void;
}) {
  const [version, setVersion] = useState("latest");
  const [twoFactorCode, setTwoFactorCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DeployResultResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const r = await triggerDeploy(env.id, {
        version: version.trim() || "latest",
        two_factor_code: twoFactorCode.trim() || null,
      });
      setResult(r);
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const ok = result && (result.status === "ok" || result.status === "success");

  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      size="md"
      title={`Deploy → ${env.name}`}
      footer={
        result ? (
          <Button onClick={onClose}>Done</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={busy}>
              {busy ? "Deploying…" : "Deploy"}
            </Button>
          </>
        )
      }
    >
      <div className="space-y-3">
        {result ? (
          <DeployResultView result={result} ok={Boolean(ok)} />
        ) : (
          <>
            <Field label="Version / tag" hint="Image tag or build label. 'latest' by default.">
              <Input
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                placeholder="v1.2.3"
                disabled={busy}
              />
            </Field>
            {env.require_2fa ? (
              <Field
                label="2FA code"
                hint="Required by this environment. Dev TOTP accepts any non-empty code."
              >
                <Input
                  value={twoFactorCode}
                  onChange={(e) => setTwoFactorCode(e.target.value)}
                  placeholder="123456"
                  disabled={busy}
                />
              </Field>
            ) : null}
            {err ? (
              <p
                role="alert"
                className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
              >
                {err}
              </p>
            ) : null}
          </>
        )}
      </div>
    </Modal>
  );
}

function DeployResultView({
  result,
  ok,
}: {
  result: DeployResultResponse;
  ok: boolean;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Badge tone={ok ? "success" : "danger"}>{result.status}</Badge>
        <span className="font-mono text-[11px] text-fg-subtle">{result.run_id}</span>
        {result.exec_code ? (
          <span className="ml-auto font-mono text-[11px] text-danger">{result.exec_code}</span>
        ) : null}
      </div>
      <pre className="max-h-[280px] overflow-auto rounded-md border border-border bg-bg p-2 font-mono text-[11px] leading-snug text-fg-muted">
        {result.log || "(no log output)"}
      </pre>
    </div>
  );
}
