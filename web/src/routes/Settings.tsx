import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Bot, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, Cloud, Copy, Cpu, ExternalLink, Eye, EyeOff, FileText, KeyRound, RefreshCw, Settings as SettingsIcon, Trash2, X, Zap } from "lucide-react";

import { type AgentPrefs, getAgentPrefs, putAgentPrefs } from "@/api/agent_prefs";
import { ApiError } from "@/api/client";
import { diagnoseSubdomainMode } from "@/api/environments";
import { type GpusResponse, getGpuInfo } from "@/api/performance";
import {
  type CloudflareConfig,
  type CloudflareConfigResponse,
  type CloudflareVerifyResponse,
  type LocalInspectionResponse,
  type MigrationDetailResponse,
  type MigrationHistoryRow,
  type MigrationScriptResponse,
  type MigrationVerifyResponse,
  type RunCutoverResponse,
  type TunnelSnapshotResponse,
  deleteCloudflareConfig,
  ensureCloudflareWildcard,
  getCloudflareConfig,
  getCloudflareTunnelSnapshot,
  getMigrationDetail,
  getMigrationScript,
  getRevertScript,
  inspectLocalCloudflared,
  listMigrationHistory,
  pushLocalToRemote,
  putCloudflareConfig,
  revertMigration,
  runCutoverScript,
  verifyCloudflareConfig,
  verifyMigration,
} from "@/api/providers";
import {
  deleteSecret,
  listSecrets,
  rotateSecret,
  storeSecret,
  type SecretView,
} from "@/api/secrets";
import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/Card";
import { ConfirmDialog } from "@/ui/ConfirmDialog";
import { Field, Input, Select } from "@/ui/Input";

/** Single user-scope secret keys we expose in the UI. The same `key_name`
 * is read back by the workspace service when it clones / runs git inside
 * the sandbox. Keep this list in sync with the server-side consumers. */
interface SecretSpec {
  key: string;
  title: string;
  description: string;
  placeholder: string;
  inputType?: "password" | "text";
}

const USER_SECRETS: SecretSpec[] = [
  {
    key: "github_token",
    title: "GitHub Personal Access Token",
    description:
      "Used to clone private repos and push branches. Grant scopes `repo` (private) or `public_repo` (public only). Stored encrypted; never returned by the API after save.",
    placeholder: "ghp_… or github_pat_…",
    inputType: "password",
  },
  {
    key: "openai_api_key",
    title: "OpenAI API Key",
    description:
      "Passed to the executor inside the sandbox as `OPENAI_API_KEY`. Used by tools that call OpenAI models.",
    placeholder: "sk-…",
    inputType: "password",
  },
  {
    key: "anthropic_api_key",
    title: "Anthropic API Key",
    description:
      "Passed to the executor inside the sandbox as `ANTHROPIC_API_KEY`. Used by the default Claude provider.",
    placeholder: "sk-ant-…",
    inputType: "password",
  },
];

// Single-admin model: every "user-scoped" secret in the legacy UI is
// now SYSTEM-scoped with owner_id=admin. The Settings page only ever
// reads/writes against this scope.
const ADMIN_SCOPE = "system" as const;
const ADMIN_OWNER_ID = "admin";

export function Settings() {
  const { t } = useI18n();
  const { me } = useAuth();
  const [secrets, setSecrets] = useState<SecretView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const items = await listSecrets({
        scope: ADMIN_SCOPE,
        owner_id: ADMIN_OWNER_ID,
      });
      setSecrets(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const byKey = useMemo(() => {
    const map = new Map<string, SecretView>();
    for (const s of secrets) map.set(s.key_name, s);
    return map;
  }, [secrets]);

  return (
    <div className="mx-auto max-w-[760px] px-6 py-8">
      <Link
        to="/projects"
        className="mb-3 inline-flex items-center gap-1 text-[12px] text-fg-muted hover:text-fg"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
        {t("nav.back_to_projects")}
      </Link>
      <header className="mb-6 flex items-center gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-bg-subtle">
          <SettingsIcon className="h-4 w-4 text-fg-muted" />
        </div>
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">{t("nav.settings")}</h1>
          <p className="text-[12px] text-fg-muted">
            Per-user credentials. Saved values are encrypted at rest and propagated to the
            sandbox for clones, git pushes, and executor tools.
          </p>
        </div>
      </header>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Profile</CardTitle>
          <CardDescription>Identity from the single-admin login.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-[13px]">
          <div className="flex justify-between gap-3">
            <span className="text-fg-muted">Display name</span>
            <span className="font-mono text-fg">{me?.display_name ?? me?.user_id ?? "—"}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-fg-muted">User ID</span>
            <span className="font-mono text-fg-subtle">{me?.user_id ?? "—"}</span>
          </div>
        </CardContent>
      </Card>

      <section className="mb-6 space-y-4">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-fg-muted" />
          <h2 className="text-[15px] font-semibold text-fg">Agent defaults</h2>
        </div>
        <AgentPrefsCard />
      </section>

      <section className="mb-6 space-y-4">
        <div className="flex items-center gap-2">
          <Cloud className="h-4 w-4 text-fg-muted" />
          <h2 className="text-[15px] font-semibold text-fg">
            {t("settings.providers.heading")}
          </h2>
        </div>
        <CloudflareProviderCard />
      </section>

      <section className="mb-6 space-y-4">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-fg-muted" />
          <h2 className="text-[15px] font-semibold text-fg">
            {t("settings.gpu.heading")}
          </h2>
        </div>
        <WorkspaceGpuCard />
      </section>

      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-fg-muted" />
          <h2 className="text-[15px] font-semibold text-fg">Credentials</h2>
        </div>

        {error ? (
          <Card className="border-danger/40">
            <CardContent className="p-3 text-[12px] text-danger">{error}</CardContent>
          </Card>
        ) : null}

        {loading ? (
          <Card>
            <CardContent className="p-4 text-[12px] text-fg-subtle">Loading…</CardContent>
          </Card>
        ) : (
          USER_SECRETS.map((spec) => (
            <SecretRow
              key={spec.key}
              spec={spec}
              existing={byKey.get(spec.key) ?? null}
              onChanged={refresh}
            />
          ))
        )}
      </section>
    </div>
  );
}

function SecretRow({
  spec,
  existing,
  onChanged,
}: {
  spec: SecretSpec;
  existing: SecretView | null;
  onChanged: () => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const configured = existing !== null;

  const save = async () => {
    if (value.trim() === "") return;
    setBusy(true);
    setErr(null);
    try {
      if (existing) {
        await rotateSecret(existing.id, value.trim());
      } else {
        await storeSecret({
          scope: ADMIN_SCOPE,
          owner_id: ADMIN_OWNER_ID,
          key_name: spec.key,
          value: value.trim(),
        });
      }
      setValue("");
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!existing) return;
    setBusy(true);
    setErr(null);
    try {
      await deleteSecret(existing.id);
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setConfirmDelete(false);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="flex-1">
          <CardTitle className="flex items-center gap-2">
            {spec.title}
            {configured ? (
              <Badge tone="success" className="gap-1">
                <CheckCircle2 className="h-3 w-3" />
                Configured
              </Badge>
            ) : (
              <Badge tone="neutral">Not set</Badge>
            )}
          </CardTitle>
          <CardDescription className="mt-1.5">{spec.description}</CardDescription>
          <p className="mt-1 font-mono text-[11px] text-fg-subtle">key_name: {spec.key}</p>
        </div>
        {configured ? (
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setConfirmDelete(true)}
            title="Delete this credential"
          >
            <Trash2 className="h-4 w-4 text-danger" />
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        <Field
          label={configured ? "Rotate value" : "Value"}
          hint={
            configured
              ? "Saved value is encrypted and not displayed. Paste a new value to rotate."
              : undefined
          }
        >
          <div className="flex gap-2">
            <Input
              type={reveal ? "text" : (spec.inputType ?? "password")}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={spec.placeholder}
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
            <Button
              variant="ghost"
              size="icon"
              type="button"
              onClick={() => setReveal((r) => !r)}
              title={reveal ? "Hide" : "Show"}
            >
              {reveal ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
            {value.length > 0 ? (
              <Button variant="ghost" size="icon" type="button" onClick={() => setValue("")}>
                <X className="h-4 w-4" />
              </Button>
            ) : null}
          </div>
        </Field>
        {err ? <p className="text-[12px] text-danger">{err}</p> : null}
        <div className="flex items-center justify-between">
          <p className="text-[11px] text-fg-subtle">
            {configured && existing
              ? `Last updated ${formatRelative(existing.rotated_at ?? existing.created_at)}`
              : " "}
          </p>
          <Button onClick={save} disabled={busy || value.trim() === ""}>
            {busy ? "Saving…" : configured ? "Rotate" : "Save"}
          </Button>
        </div>
      </CardContent>
      <ConfirmDialog
        open={confirmDelete}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={remove}
        title="Delete credential?"
        description={`Remove ${spec.title} from your account? Workspaces created afterwards will not have access to this credential.`}
        confirmLabel="Delete"
        cancelLabel="Cancel"
        tone="danger"
        busy={busy}
      />
    </Card>
  );
}

function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return iso;
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// Model choices: the values geny-executor's `_route_model` accepts.
// "사용자 기본값 유지" sentinel = empty string = clear the override.
const MODEL_CHOICES: { value: string; label: string }[] = [
  { value: "", label: "Use manifest default (gapt_default = sonnet)" },
  { value: "sonnet", label: "Claude Sonnet 4.6 (fast, cheap, recommended)" },
  { value: "opus", label: "Claude Opus 4.7 (smarter, slower, ~10× cost)" },
  { value: "haiku", label: "Claude Haiku 4.5 (fastest, cheapest)" },
];

interface AgentPrefsFormState {
  model: string;
  max_tokens: string;
  max_iterations: string;
  cost_budget_usd: string;
  timeout_s: string;
  permission_mode: string;
}

function emptyForm(): AgentPrefsFormState {
  return {
    model: "",
    max_tokens: "",
    max_iterations: "",
    cost_budget_usd: "",
    timeout_s: "",
    permission_mode: "",
  };
}

function prefsToForm(prefs: AgentPrefs): AgentPrefsFormState {
  return {
    model: prefs.model ?? "",
    max_tokens: prefs.max_tokens != null ? String(prefs.max_tokens) : "",
    max_iterations: prefs.max_iterations != null ? String(prefs.max_iterations) : "",
    cost_budget_usd: prefs.cost_budget_usd != null ? String(prefs.cost_budget_usd) : "",
    timeout_s: prefs.timeout_s != null ? String(prefs.timeout_s) : "",
    permission_mode: prefs.permission_mode ?? "",
  };
}

function formToPayload(form: AgentPrefsFormState): AgentPrefs {
  const numOrNull = (s: string): number | null => {
    const t = s.trim();
    if (t === "") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  };
  const pm = form.permission_mode.trim();
  return {
    model: form.model.trim() === "" ? null : form.model.trim(),
    max_tokens: numOrNull(form.max_tokens),
    max_iterations: numOrNull(form.max_iterations),
    cost_budget_usd: numOrNull(form.cost_budget_usd),
    timeout_s: numOrNull(form.timeout_s),
    permission_mode: pm === "" ? null : (pm as AgentPrefs["permission_mode"]),
  };
}

// CLI permission modes — controls whether spawned `claude` CLI auto-
// approves tool calls. "bypassPermissions" is the only one that
// behaves correctly in our headless flow; the others either prompt
// (CLI hangs) or restrict tool use.
const PERMISSION_CHOICES: { value: string; label: string }[] = [
  { value: "", label: "Server default (bypassPermissions — recommended)" },
  { value: "bypassPermissions", label: "bypassPermissions — auto-allow every tool call" },
  { value: "acceptEdits", label: "acceptEdits — auto-allow edits, prompt for risky" },
  { value: "plan", label: "plan — read-only mode (no edits, no Bash)" },
  { value: "default", label: "default — prompt every call (likely to hang headless)" },
];

function AgentPrefsCard() {
  const [form, setForm] = useState<AgentPrefsFormState>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const prefs = await getAgentPrefs();
      setForm(prefsToForm(prefs));
      setSavedAt(prefs.updated_at ?? null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const next = await putAgentPrefs(formToPayload(form));
      setForm(prefsToForm(next));
      setSavedAt(next.updated_at ?? new Date().toISOString());
    } catch (e) {
      setErr(e instanceof ApiError ? e.reason : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onField =
    (key: keyof AgentPrefsFormState) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      // Capture the value *outside* the updater. React reuses the
      // synthetic event object, so by the time the updater runs the
      // `currentTarget` may be null — exactly the `Cannot read
      // properties of null (reading 'value')` crash the user hit
      // when picking a different model in the dropdown.
      const value = e.target.value;
      setForm((prev) => ({ ...prev, [key]: value }));
    };

  if (loading) {
    return (
      <Card>
        <CardContent className="p-4 text-[12px] text-fg-subtle">Loading…</CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Pipeline overrides</CardTitle>
        <CardDescription>
          Override the bundled <code>gapt_default</code> manifest for every chat session you
          start. Leave a field blank to use the manifest's default. Per-project overrides
          ship later; this is the global baseline.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field
            label="Model"
            hint="Maps to stage 6 (api). Sonnet is the default; Opus costs ~10× more per token."
          >
            <Select value={form.model} onChange={onField("model")} disabled={busy}>
              {MODEL_CHOICES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="Max output tokens"
            hint="Per API call. Bundled default = 8192. 1–200000."
          >
            <Input
              type="number"
              min={1}
              max={200000}
              placeholder="8192"
              value={form.max_tokens}
              onChange={onField("max_tokens")}
              disabled={busy}
            />
          </Field>
          <Field
            label="Max iterations"
            hint="Hard cap on agent loop iterations. Bundled default = 10."
          >
            <Input
              type="number"
              min={1}
              max={100}
              placeholder="10"
              value={form.max_iterations}
              onChange={onField("max_iterations")}
              disabled={busy}
            />
          </Field>
          <Field
            label="Cost budget (USD)"
            hint="Per session. Bundled default = $1.00. Pipeline stops when this is hit."
          >
            <Input
              type="number"
              min={0}
              step={0.01}
              placeholder="1.00"
              value={form.cost_budget_usd}
              onChange={onField("cost_budget_usd")}
              disabled={busy}
            />
          </Field>
          <Field
            label="API timeout (s)"
            hint="Per CLI subprocess invocation. Bundled default = 180s. 1–600."
          >
            <Input
              type="number"
              min={1}
              max={600}
              placeholder="180"
              value={form.timeout_s}
              onChange={onField("timeout_s")}
              disabled={busy}
            />
          </Field>
          <Field
            label="Permission mode"
            hint="Controls whether the spawned Claude CLI auto-approves tool calls (Read / Edit / Bash). The default is the only mode that works headless — the others either prompt (and hang) or restrict tool use."
          >
            <Select
              value={form.permission_mode}
              onChange={onField("permission_mode")}
              disabled={busy}
            >
              {PERMISSION_CHOICES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {err ? <p className="text-[12px] text-danger">{err}</p> : null}
        <div className="flex items-center justify-between">
          <p className="text-[11px] text-fg-subtle">
            {savedAt ? `Saved ${formatRelative(savedAt)}` : "Not yet saved"}
          </p>
          <Button onClick={save} disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ────────────────────────────────────────── Cloudflare provider card ──

function describeApiError(e: unknown): string {
  if (e instanceof ApiError) return e.reason;
  return e instanceof Error ? e.message : String(e);
}

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
      className="inline-flex items-center gap-1 rounded border border-border bg-bg px-1.5 py-0.5 text-[10.5px] text-fg-muted hover:bg-bg-subtle hover:text-fg"
      title="Copy to clipboard"
    >
      {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
      {label ?? (copied ? "Copied" : "Copy")}
    </button>
  );
}

function CloudflareTokenGuide() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  // Cloudflare's "Create API Token" page. The deep-link templateName
  // param is undocumented and unstable across regions — we just open
  // the generic page and explain which scopes to add.
  const tokenPageUrl = "https://dash.cloudflare.com/profile/api-tokens";
  const scopeAccountTunnel = "Account → Cloudflare Tunnel → Edit";
  const scopeZoneRead = "Zone → Zone → Read";
  const scopeDnsEdit = "Zone → DNS → Edit  (optional, for future DNS automation)";

  return (
    <div className="rounded-md border border-border bg-bg-subtle">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[12px] font-medium text-fg hover:bg-bg"
      >
        <span className="inline-flex items-center gap-1.5">
          <FileText className="h-3.5 w-3.5 text-accent" />
          {t("settings.providers.cloudflare.guide.title")}
        </span>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
      </button>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3 text-[11.5px] leading-relaxed text-fg-muted">
          <ol className="ml-4 list-decimal space-y-2.5">
            <li>
              <p>
                {t("settings.providers.cloudflare.guide.step1.intro")}{" "}
                <a
                  href={tokenPageUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-0.5 text-accent hover:underline"
                >
                  {t("settings.providers.cloudflare.guide.step1.link_label")}
                  <ExternalLink className="h-3 w-3" />
                </a>
              </p>
              <p className="mt-1 text-fg-subtle">
                {t("settings.providers.cloudflare.guide.step1.detail")}
              </p>
            </li>
            <li>
              <p>{t("settings.providers.cloudflare.guide.step2.intro")}</p>
              <div className="mt-1.5 space-y-1 rounded border border-border bg-bg px-2 py-1.5 font-mono text-[10.5px] text-fg">
                <div className="flex items-center justify-between gap-2">
                  <span>{scopeAccountTunnel}</span>
                  <CopyButton text={scopeAccountTunnel} />
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span>{scopeZoneRead}</span>
                  <CopyButton text={scopeZoneRead} />
                </div>
                <div className="flex items-center justify-between gap-2 text-fg-muted">
                  <span>{scopeDnsEdit}</span>
                  <CopyButton text={scopeDnsEdit} />
                </div>
              </div>
              <p className="mt-1 text-fg-subtle">
                {t("settings.providers.cloudflare.guide.step2.detail")}
              </p>
            </li>
            <li>
              <p>{t("settings.providers.cloudflare.guide.step3.intro")}</p>
              <p className="mt-1 text-fg-subtle">
                {t("settings.providers.cloudflare.guide.step3.detail")}
              </p>
            </li>
            <li>
              <p>{t("settings.providers.cloudflare.guide.step4.intro")}</p>
              <p className="mt-1 text-fg-subtle">
                {t("settings.providers.cloudflare.guide.step4.detail")}
              </p>
            </li>
          </ol>
          <div className="rounded border border-warn/40 bg-warn/5 px-2 py-1.5">
            <p className="flex items-start gap-1.5 text-[11px] text-warn">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              <span>{t("settings.providers.cloudflare.guide.security_note")}</span>
            </p>
          </div>
        </div>
      ) : null}
    </div>
  );
}

type MigrationStep = "inspect" | "push" | "cutover" | "verify";

function MigrationWizard({
  configured,
  accountId,
  tunnelId,
  onTunnelDetected,
  onSnapshotChanged,
  onMigrationRecorded,
}: {
  configured: boolean;
  accountId: string | null;
  tunnelId: string | null;
  onTunnelDetected: (uuid: string) => void;
  onSnapshotChanged: (snapshot: TunnelSnapshotResponse) => void;
  /** Fired whenever a provider_migrations row is created (dry-run
   *  or live), so the history section below can refresh. */
  onMigrationRecorded?: () => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<MigrationStep>("inspect");
  const [inspect, setInspect] = useState<LocalInspectionResponse | null>(null);
  const [pushedSnapshot, setPushedSnapshot] = useState<TunnelSnapshotResponse | null>(null);
  const [script, setScript] = useState<MigrationScriptResponse | null>(null);
  const [verifyResult, setVerifyResult] = useState<MigrationVerifyResponse | null>(null);
  const [busy, setBusy] = useState<MigrationStep | "autorun" | "dryrun" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Auto-run state — password + last run result. Password is kept
  // in component state only, cleared on success and never logged.
  const [sudoPassword, setSudoPassword] = useState("");
  const [showSudoPassword, setShowSudoPassword] = useState(false);
  const [autorunResult, setAutorunResult] = useState<RunCutoverResponse | null>(
    null,
  );
  const [dryRunResult, setDryRunResult] = useState<RunCutoverResponse | null>(null);

  const runDryRun = async () => {
    setBusy("dryrun");
    setErr(null);
    setDryRunResult(null);
    try {
      const r = await runCutoverScript({
        dry_run: true,
        tunnel_id: tunnelId ?? inspect?.tunnel_uuid ?? undefined,
      });
      setDryRunResult(r);
      if (r.migration_id) onMigrationRecorded?.();
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const runAutorun = async () => {
    setBusy("autorun");
    setErr(null);
    setAutorunResult(null);
    try {
      const r = await runCutoverScript({
        sudo_password: sudoPassword || undefined,
        tunnel_id: tunnelId ?? inspect?.tunnel_uuid ?? undefined,
      });
      setAutorunResult(r);
      if (r.migration_id) onMigrationRecorded?.();
      if (r.ok) {
        // Wipe the password buffer the moment the command succeeds.
        setSudoPassword("");
        // Advance to verify so the operator confirms remote state
        // — even cutover success doesn't guarantee the new mode
        // until Cloudflare reports `remote_managed`.
        setStep("verify");
      }
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const runInspect = async () => {
    setBusy("inspect");
    setErr(null);
    try {
      const r = await inspectLocalCloudflared();
      setInspect(r);
      if (r.tunnel_uuid) onTunnelDetected(r.tunnel_uuid);
      if (r.exists && r.readable) setStep("push");
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const runPush = async () => {
    setBusy("push");
    setErr(null);
    try {
      // Pass the form's draft values so push works even when the
      // operator hasn't clicked Save yet — backend persists these
      // on success.
      const r = await pushLocalToRemote({
        account_id: accountId ?? undefined,
        tunnel_id: tunnelId ?? inspect?.tunnel_uuid ?? undefined,
      });
      setPushedSnapshot(r);
      onSnapshotChanged(r);
      setStep("cutover");
      // Auto-fetch the cutover script as soon as push succeeds —
      // saves a click.
      const s = await getMigrationScript();
      setScript(s);
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const runVerify = async () => {
    setBusy("verify");
    setErr(null);
    try {
      const r = await verifyMigration();
      setVerifyResult(r);
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const stepStatus = (s: MigrationStep): "done" | "active" | "pending" => {
    const order: MigrationStep[] = ["inspect", "push", "cutover", "verify"];
    const cur = order.indexOf(step);
    const idx = order.indexOf(s);
    if (idx < cur) return "done";
    if (idx === cur) return "active";
    return "pending";
  };

  return (
    <div className="rounded-md border border-border bg-bg-subtle">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[12px] font-medium text-fg hover:bg-bg"
      >
        <span className="inline-flex items-center gap-1.5">
          <Zap className="h-3.5 w-3.5 text-accent" />
          {t("settings.providers.cloudflare.migration.title")}
        </span>
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
      </button>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          <p className="text-[11.5px] leading-relaxed text-fg-muted">
            {t("settings.providers.cloudflare.migration.intro")}
          </p>

          {/* Stepper */}
          <div className="flex items-center gap-1 text-[10.5px] font-medium uppercase tracking-wider">
            {(["inspect", "push", "cutover", "verify"] as MigrationStep[]).map(
              (s, i, arr) => (
                <div key={s} className="flex items-center gap-1">
                  <span
                    className={
                      stepStatus(s) === "done"
                        ? "rounded-full border border-success/40 bg-success/10 px-1.5 py-0.5 text-success"
                        : stepStatus(s) === "active"
                          ? "rounded-full border border-accent/40 bg-accent/10 px-1.5 py-0.5 text-accent"
                          : "rounded-full border border-border bg-bg px-1.5 py-0.5 text-fg-subtle"
                    }
                  >
                    {i + 1}. {t(`settings.providers.cloudflare.migration.step.${s}`)}
                  </span>
                  {i < arr.length - 1 ? (
                    <span className="text-fg-subtle">→</span>
                  ) : null}
                </div>
              ),
            )}
          </div>

          {/* Step 1: Inspect */}
          {step === "inspect" ? (
            <div className="space-y-2">
              <p className="text-[11.5px] text-fg-muted">
                {t("settings.providers.cloudflare.migration.inspect.detail")}
              </p>
              <Button onClick={runInspect} disabled={!!busy}>
                {busy === "inspect"
                  ? "Reading…"
                  : t("settings.providers.cloudflare.migration.inspect.button")}
              </Button>
            </div>
          ) : null}

          {/* Step 2: Push */}
          {step === "push" && inspect ? (
            <div className="space-y-2">
              <div className="rounded border border-border bg-bg px-2.5 py-2 text-[11px]">
                <p className="mb-1 font-medium text-fg">
                  {t("settings.providers.cloudflare.migration.push.read_from")}
                  <span className="ml-1 font-mono text-fg-subtle">{inspect.path}</span>
                </p>
                <p className="text-fg-muted">
                  tunnel:{" "}
                  <span className="font-mono">
                    {inspect.tunnel_id ?? "—"}
                    {inspect.tunnel_uuid && inspect.tunnel_uuid !== inspect.tunnel_id
                      ? ` (uuid ${inspect.tunnel_uuid.slice(0, 8)}…)`
                      : ""}
                  </span>
                </p>
                <p className="mt-1 text-fg-muted">
                  ingress entries: <span className="font-mono">{inspect.ingress.length}</span>
                </p>
                <ul className="mt-1 space-y-0.5 font-mono text-[10.5px] text-fg-subtle">
                  {inspect.ingress.map((e, i) => (
                    <li key={i}>
                      {e.hostname || "<catch-all>"} → {e.service}
                    </li>
                  ))}
                </ul>
              </div>
              {!configured || !accountId ? (
                <p className="text-[11px] text-warn">
                  {t("settings.providers.cloudflare.migration.push.need_verify")}
                </p>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  onClick={runPush}
                  disabled={
                    !!busy ||
                    !configured ||
                    !accountId ||
                    !(tunnelId || inspect.tunnel_uuid)
                  }
                >
                  {busy === "push"
                    ? "Pushing…"
                    : t("settings.providers.cloudflare.migration.push.button")}
                </Button>
                <Button variant="ghost" onClick={() => setStep("inspect")}>
                  {t("settings.providers.cloudflare.migration.back")}
                </Button>
              </div>
            </div>
          ) : null}

          {/* Step 3: Cutover */}
          {step === "cutover" ? (
            <div className="space-y-2">
              {pushedSnapshot ? (
                <p className="text-[11px] text-success">
                  ✓ {t("settings.providers.cloudflare.migration.cutover.pushed_ok")} (
                  {pushedSnapshot.ingress.length} entries)
                </p>
              ) : null}
              <p className="text-[11.5px] text-fg-muted">
                {t("settings.providers.cloudflare.migration.cutover.detail")}
              </p>
              {script ? (
                <>
                  <div className="rounded border border-border bg-bg">
                    <div className="flex items-center justify-between border-b border-border px-2 py-1">
                      <span className="font-mono text-[10.5px] text-fg-muted">
                        {script.filename}
                      </span>
                      <CopyButton text={script.script} label="Copy script" />
                    </div>
                    <pre className="max-h-64 overflow-auto px-2.5 py-2 font-mono text-[10.5px] leading-snug text-fg">
                      {script.script}
                    </pre>
                  </div>
                  <div className="rounded border border-accent/40 bg-accent/5 px-2 py-1.5">
                    <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-accent">
                      {t("settings.providers.cloudflare.migration.cutover.autorun_label")}
                    </p>
                    <p className="text-[11px] text-fg-muted">
                      {t("settings.providers.cloudflare.migration.cutover.autorun_hint")}
                    </p>
                    <div className="mt-1.5 flex gap-2">
                      <Input
                        type={showSudoPassword ? "text" : "password"}
                        value={sudoPassword}
                        onChange={(e) => setSudoPassword(e.target.value)}
                        placeholder="sudo password (leave blank if NOPASSWD configured)"
                        autoComplete="current-password"
                        spellCheck={false}
                        disabled={!!busy}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !busy) {
                            e.preventDefault();
                            void runAutorun();
                          }
                        }}
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        type="button"
                        onClick={() => setShowSudoPassword((s) => !s)}
                        title={showSudoPassword ? "Hide" : "Show"}
                      >
                        {showSudoPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </Button>
                      <Button
                        onClick={runAutorun}
                        disabled={!!busy}
                        variant="primary"
                      >
                        <Zap className="mr-1 h-3.5 w-3.5" />
                        {busy === "autorun"
                          ? "Running…"
                          : t("settings.providers.cloudflare.migration.cutover.autorun_button")}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={runDryRun}
                        disabled={!!busy}
                        type="button"
                        title={t("settings.providers.cloudflare.migration.dryrun.hint")}
                      >
                        {busy === "dryrun"
                          ? "Previewing…"
                          : t("settings.providers.cloudflare.migration.dryrun.button")}
                      </Button>
                    </div>
                    <p className="mt-1.5 text-[11px] text-fg-subtle">
                      {t("settings.providers.cloudflare.migration.cutover.password_disclosure")}
                    </p>
                    {dryRunResult ? (
                      <div className="mt-2 rounded border border-accent/40 bg-accent/5 px-2 py-1.5">
                        <p className="mb-1 text-[11px] font-medium text-accent">
                          {t("settings.providers.cloudflare.migration.dryrun.banner")}
                        </p>
                        <pre className="max-h-40 overflow-auto rounded bg-bg px-2 py-1 font-mono text-[10.5px] leading-snug text-fg">
                          {dryRunResult.stdout}
                        </pre>
                      </div>
                    ) : null}
                    {autorunResult ? (
                      <div
                        className={
                          autorunResult.ok
                            ? "mt-2 rounded border border-success/40 bg-success/5 px-2 py-1.5"
                            : "mt-2 rounded border border-danger/40 bg-danger/5 px-2 py-1.5"
                        }
                      >
                        <p
                          className={
                            autorunResult.ok
                              ? "mb-1 text-[11px] font-medium text-success"
                              : "mb-1 text-[11px] font-medium text-danger"
                          }
                        >
                          {autorunResult.ok ? "✓ " : "✗ "}
                          {autorunResult.message}
                        </p>
                        {autorunResult.stdout ? (
                          <pre className="max-h-32 overflow-auto rounded bg-bg px-2 py-1 font-mono text-[10.5px] leading-snug text-fg">
                            {autorunResult.stdout}
                          </pre>
                        ) : null}
                        {autorunResult.stderr ? (
                          <pre className="mt-1 max-h-32 overflow-auto rounded bg-bg px-2 py-1 font-mono text-[10.5px] leading-snug text-warn">
                            {autorunResult.stderr}
                          </pre>
                        ) : null}
                      </div>
                    ) : null}
                  </div>

                  <div className="rounded border border-warn/40 bg-warn/5 px-2 py-1.5">
                    <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-warn">
                      {t("settings.providers.cloudflare.migration.cutover.run_label")}
                    </p>
                    <p className="text-[11px] text-fg-muted">
                      {t("settings.providers.cloudflare.migration.cutover.run_hint")}
                    </p>
                    <div className="mt-1.5 flex items-center justify-between gap-2 rounded bg-bg px-2 py-1 font-mono text-[10.5px] text-fg">
                      <span className="truncate">
                        sudo bash /tmp/{script.filename}
                      </span>
                      <CopyButton text={`sudo bash /tmp/${script.filename}`} />
                    </div>
                    <p className="mt-1.5 text-[11px] text-fg-muted">
                      {t("settings.providers.cloudflare.migration.cutover.write_hint")}
                    </p>
                  </div>
                </>
              ) : (
                <p className="text-[11px] text-fg-subtle">Loading script…</p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => setStep("verify")}>
                  {t("settings.providers.cloudflare.migration.cutover.continue")}
                </Button>
                <Button variant="ghost" onClick={() => setStep("push")}>
                  {t("settings.providers.cloudflare.migration.back")}
                </Button>
              </div>
            </div>
          ) : null}

          {/* Step 4: Verify */}
          {step === "verify" ? (
            <div className="space-y-2">
              <p className="text-[11.5px] text-fg-muted">
                {t("settings.providers.cloudflare.migration.verify.detail")}
              </p>
              <Button onClick={runVerify} disabled={!!busy}>
                <RefreshCw className="mr-1 h-3.5 w-3.5" />
                {busy === "verify"
                  ? "Verifying…"
                  : t("settings.providers.cloudflare.migration.verify.button")}
              </Button>
              {verifyResult ? (
                <div
                  className={
                    verifyResult.ok
                      ? "rounded border border-success/40 bg-success/5 px-2 py-1.5"
                      : "rounded border border-warn/40 bg-warn/5 px-2 py-1.5"
                  }
                >
                  <p
                    className={
                      verifyResult.ok
                        ? "text-[11px] font-medium text-success"
                        : "text-[11px] font-medium text-warn"
                    }
                  >
                    {verifyResult.ok ? "✓ " : "⚠ "}
                    mode={verifyResult.mode}, {verifyResult.connection_summary}
                  </p>
                  <p className="mt-1 text-[11px] text-fg-muted">{verifyResult.message}</p>
                </div>
              ) : null}
              <Button variant="ghost" onClick={() => setStep("cutover")}>
                {t("settings.providers.cloudflare.migration.back")}
              </Button>
            </div>
          ) : null}

          {err ? <p className="text-[11px] text-danger">{err}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────── Migration history ──
//
// Shows the `provider_migrations` audit table — past cutover
// attempts on this GAPT install, with one-click revert when the
// row isn't already rolled back. The Inspect button opens a
// modal with the before/after JSON snapshots so the operator can
// diff what changed.

type RevertTarget = {
  id: string;
  kind: string;
  startedAt: string;
};

function MigrationHistorySection({ refreshKey }: { refreshKey: number }) {
  const { t } = useI18n();
  const [rows, setRows] = useState<MigrationHistoryRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<MigrationDetailResponse | null>(null);
  const [revertTarget, setRevertTarget] = useState<RevertTarget | null>(null);
  const [revertPassword, setRevertPassword] = useState("");
  const [showRevertPassword, setShowRevertPassword] = useState(false);
  const [reverting, setReverting] = useState(false);
  const [revertErr, setRevertErr] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await listMigrationHistory();
      setRows(r);
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload, refreshKey]);

  const openDetail = async (id: string) => {
    setErr(null);
    try {
      const r = await getMigrationDetail(id);
      setDetail(r);
    } catch (e) {
      setErr(describeApiError(e));
    }
  };

  const confirmRevert = async () => {
    if (!revertTarget) return;
    setReverting(true);
    setRevertErr(null);
    try {
      const r = await revertMigration(revertTarget.id, {
        sudo_password: revertPassword || undefined,
      });
      if (!r.ok) {
        setRevertErr(r.message);
        return;
      }
      // Success — wipe password, close dialog, refresh list.
      setRevertPassword("");
      setRevertTarget(null);
      await reload();
    } catch (e) {
      setRevertErr(describeApiError(e));
    } finally {
      setReverting(false);
    }
  };

  const statusTone = (
    s: string,
  ): "success" | "warn" | "danger" | "neutral" => {
    if (s === "ok") return "success";
    if (s === "rolled_back") return "warn";
    if (s === "failed") return "danger";
    return "neutral";
  };

  const statusLabel = (s: string): string => {
    switch (s) {
      case "ok":
        return t("settings.providers.cloudflare.migration.history.status.ok");
      case "failed":
        return t("settings.providers.cloudflare.migration.history.status.failed");
      case "dry_run":
        return t("settings.providers.cloudflare.migration.history.status.dry_run");
      case "in_progress":
        return t("settings.providers.cloudflare.migration.history.status.in_progress");
      case "rolled_back":
        return t("settings.providers.cloudflare.migration.history.status.rolled_back");
      default:
        return s;
    }
  };

  return (
    <div className="rounded-md border border-border bg-bg-subtle">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex-1">
          <p className="text-[12px] font-medium text-fg">
            {t("settings.providers.cloudflare.migration.history.title")}
          </p>
          <p className="mt-0.5 text-[11px] text-fg-muted">
            {t("settings.providers.cloudflare.migration.history.hint")}
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={reload}
          disabled={loading}
          title={t("settings.providers.cloudflare.migration.history.refresh")}
        >
          <RefreshCw className={loading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
        </Button>
      </div>
      <div className="px-3 py-2">
        {err ? <p className="mb-2 text-[11px] text-danger">{err}</p> : null}
        {rows === null && loading ? (
          <p className="text-[11px] text-fg-subtle">Loading…</p>
        ) : rows && rows.length === 0 ? (
          <p className="text-[11px] text-fg-subtle">
            {t("settings.providers.cloudflare.migration.history.empty")}
          </p>
        ) : rows ? (
          <table className="w-full border-collapse text-[11px]">
            <thead>
              <tr className="border-b border-border text-left text-fg-muted">
                <th className="py-1 pr-2 font-medium">
                  {t("settings.providers.cloudflare.migration.history.col.when")}
                </th>
                <th className="py-1 pr-2 font-medium">
                  {t("settings.providers.cloudflare.migration.history.col.kind")}
                </th>
                <th className="py-1 pr-2 font-medium">
                  {t("settings.providers.cloudflare.migration.history.col.status")}
                </th>
                <th className="py-1 pr-2 font-medium text-right">
                  {t("settings.providers.cloudflare.migration.history.col.actions")}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const reverted = r.rolled_back_at !== null || r.status === "rolled_back";
                return (
                  <tr key={r.id} className="border-b border-border/60 last:border-0">
                    <td className="py-1 pr-2 text-fg-subtle">
                      {formatRelative(r.started_at)}
                    </td>
                    <td className="py-1 pr-2 font-mono text-fg-muted">{r.kind}</td>
                    <td className="py-1 pr-2">
                      <Badge tone={statusTone(r.status)}>{statusLabel(r.status)}</Badge>
                      {r.error ? (
                        <span
                          title={r.error}
                          className="ml-1 inline-block max-w-[160px] truncate align-middle text-[10.5px] text-fg-subtle"
                        >
                          {r.error}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-1 pr-2 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void openDetail(r.id)}
                      >
                        {t("settings.providers.cloudflare.migration.history.view")}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={reverted || r.status === "in_progress"}
                        onClick={() =>
                          setRevertTarget({
                            id: r.id,
                            kind: r.kind,
                            startedAt: r.started_at,
                          })
                        }
                        title={
                          reverted
                            ? t("settings.providers.cloudflare.migration.history.already_reverted")
                            : undefined
                        }
                      >
                        {t("settings.providers.cloudflare.migration.history.revert")}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : null}
      </div>

      {/* Detail modal */}
      {detail ? (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setDetail(null);
          }}
        >
          <div className="max-h-[80vh] w-full max-w-3xl overflow-auto rounded-md border border-border bg-bg shadow-lg">
            <div className="flex items-center justify-between border-b border-border px-3 py-2">
              <p className="text-[12px] font-medium text-fg">
                {t("settings.providers.cloudflare.migration.history.detail.title")}
                <span className="ml-2 font-mono text-fg-subtle">{detail.id}</span>
              </p>
              <Button variant="ghost" size="icon" onClick={() => setDetail(null)}>
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="space-y-3 px-3 py-3">
              <div>
                <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-fg-muted">
                  {t("settings.providers.cloudflare.migration.history.detail.before")}
                </p>
                <pre className="max-h-64 overflow-auto rounded border border-border bg-bg-subtle px-2 py-1.5 font-mono text-[10.5px] leading-snug text-fg">
                  {JSON.stringify(detail.before_snapshot, null, 2)}
                </pre>
              </div>
              <div>
                <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-fg-muted">
                  {t("settings.providers.cloudflare.migration.history.detail.after")}
                </p>
                <pre className="max-h-64 overflow-auto rounded border border-border bg-bg-subtle px-2 py-1.5 font-mono text-[10.5px] leading-snug text-fg">
                  {JSON.stringify(detail.after_snapshot, null, 2)}
                </pre>
              </div>
              <div className="flex justify-end">
                <Button variant="ghost" onClick={() => setDetail(null)}>
                  {t("settings.providers.cloudflare.migration.history.detail.close")}
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* Revert confirmation — needs sudo password since the revert
          script runs the same systemctl/rm-drop-in commands as the
          original cutover. */}
      {revertTarget ? (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
          onClick={(e) => {
            if (e.target === e.currentTarget && !reverting) {
              setRevertTarget(null);
              setRevertPassword("");
              setRevertErr(null);
            }
          }}
        >
          <div className="w-full max-w-md rounded-md border border-border bg-bg shadow-lg">
            <div className="border-b border-border px-3 py-2">
              <p className="text-[12px] font-medium text-fg">
                {t("settings.providers.cloudflare.migration.history.revert.confirm_title")}
              </p>
              <p className="mt-0.5 font-mono text-[10.5px] text-fg-subtle">
                {revertTarget.kind} · {formatRelative(revertTarget.startedAt)}
              </p>
            </div>
            <div className="space-y-3 px-3 py-3">
              <p className="text-[11.5px] leading-relaxed text-fg-muted">
                {t("settings.providers.cloudflare.migration.history.revert.confirm_body")}
              </p>
              <Field
                label={t("settings.providers.cloudflare.migration.history.revert.password_label")}
              >
                <div className="flex gap-2">
                  <Input
                    type={showRevertPassword ? "text" : "password"}
                    value={revertPassword}
                    onChange={(e) => setRevertPassword(e.target.value)}
                    autoComplete="current-password"
                    spellCheck={false}
                    disabled={reverting}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !reverting) {
                        e.preventDefault();
                        void confirmRevert();
                      }
                    }}
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    type="button"
                    onClick={() => setShowRevertPassword((s) => !s)}
                    title={showRevertPassword ? "Hide" : "Show"}
                  >
                    {showRevertPassword ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              </Field>
              {revertErr ? (
                <p className="text-[11px] text-danger">{revertErr}</p>
              ) : null}
              <div className="flex items-center justify-end gap-2">
                <Button
                  variant="ghost"
                  onClick={() => {
                    setRevertTarget(null);
                    setRevertPassword("");
                    setRevertErr(null);
                  }}
                  disabled={reverting}
                >
                  Cancel
                </Button>
                <Button onClick={confirmRevert} disabled={reverting} variant="primary">
                  {reverting
                    ? t("settings.providers.cloudflare.migration.history.reverting")
                    : t("settings.providers.cloudflare.migration.history.revert.run")}
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function CloudflareProviderCard() {
  const { t } = useI18n();
  const [resp, setResp] = useState<CloudflareConfigResponse | null>(null);
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [verifyResult, setVerifyResult] = useState<CloudflareVerifyResponse | null>(null);
  const [snapshot, setSnapshot] = useState<TunnelSnapshotResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Bump after MigrationWizard records a new audit row so the
  // history table below re-fetches without manual refresh.
  const [migrationTick, setMigrationTick] = useState(0);

  // Local edit buffer for non-secret config — diverges from `resp.config`
  // only between user edit and Save.
  const [draft, setDraft] = useState<CloudflareConfig>({
    account_id: null,
    zone_id: null,
    tunnel_id: null,
    preview_domain: null,
    upstream: null,
  });

  const refresh = useCallback(async () => {
    setErr(null);
    try {
      const r = await getCloudflareConfig();
      setResp(r);
      setDraft(r.config);
    } catch (e) {
      setErr(describeApiError(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = async () => {
    setBusy("save");
    setErr(null);
    try {
      const r = await putCloudflareConfig({
        api_token: token.trim() === "" ? undefined : token.trim(),
        config: draft,
      });
      setResp(r);
      setDraft(r.config);
      setToken("");
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const verify = async () => {
    setBusy("verify");
    setErr(null);
    try {
      const r = await verifyCloudflareConfig();
      setVerifyResult(r);

      // Pull server-side preview_domain from the diagnose endpoint
      // so the form can pre-fill it from `GAPT_CADDY_PREVIEW_DOMAIN`
      // when the operator hasn't typed it yet. Same idea for the
      // local tunnel UUID below.
      let serverPreviewDomain: string | null = null;
      try {
        const d = await diagnoseSubdomainMode();
        serverPreviewDomain = d.preview_domain;
      } catch {
        // ignore — diagnose can fail on a totally fresh install.
      }

      // Best-effort: read the host's cloudflared config.yml to find
      // the UUID of the tunnel actually running on this machine.
      // Used below to pre-select that tunnel when the account has
      // multiple — saves the operator guessing which name in the
      // dropdown is "theirs". Permission errors are absorbed —
      // they're informational here, not blocking.
      let localTunnelUuid: string | null = null;
      try {
        const local = await inspectLocalCloudflared();
        localTunnelUuid = local.tunnel_uuid;
      } catch {
        // ignore — user may be running in a sandboxed env where
        // /etc/cloudflared isn't reachable.
      }

      // Auto-select obvious singletons + match the local tunnel
      // when there are multiple. Operator can always change the
      // selection manually. Also pre-fill preview_domain + upstream
      // when blank so the operator doesn't have to retype values
      // GAPT already knows.
      setDraft((d) => {
        const next = { ...d };
        if (!next.account_id && r.accounts.length === 1) {
          next.account_id = r.accounts[0].id;
        }
        const aid = next.account_id;
        if (aid && !next.tunnel_id) {
          const t = r.tunnels_by_account[aid] ?? [];
          // Prefer the tunnel that matches local config.yml,
          // otherwise fall back to the single-option case.
          const matched = localTunnelUuid
            ? t.find((tn) => tn.id === localTunnelUuid)
            : null;
          if (matched) next.tunnel_id = matched.id;
          else if (t.length === 1) next.tunnel_id = t[0].id;
        }
        if (!next.zone_id) {
          const candidateZones = aid
            ? r.zones.filter((z) => z.account_id === aid)
            : r.zones;
          if (candidateZones.length === 1) next.zone_id = candidateZones[0].id;
        }
        if (!next.preview_domain && serverPreviewDomain) {
          next.preview_domain = serverPreviewDomain;
        }
        if (!next.upstream) {
          next.upstream = "http://localhost:38080";
        }
        return next;
      });
      void refresh();
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const fetchSnapshot = async () => {
    setBusy("snapshot");
    setErr(null);
    try {
      const r = await getCloudflareTunnelSnapshot();
      setSnapshot(r);
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const ensureWildcard = async () => {
    setBusy("wildcard");
    setErr(null);
    try {
      const r = await ensureCloudflareWildcard();
      setSnapshot(r);
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    setBusy("delete");
    setErr(null);
    try {
      await deleteCloudflareConfig();
      setResp(null);
      setVerifyResult(null);
      setSnapshot(null);
      setDraft({
        account_id: null,
        zone_id: null,
        tunnel_id: null,
        preview_domain: null,
        upstream: null,
      });
      void refresh();
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setBusy(null);
      setConfirmDelete(false);
    }
  };

  const configured = resp?.configured === true;
  const tunnels = useMemo(() => {
    if (!verifyResult || !draft.account_id) return [];
    return verifyResult.tunnels_by_account[draft.account_id] ?? [];
  }, [verifyResult, draft.account_id]);
  const zonesForAccount = useMemo(() => {
    if (!verifyResult) return [];
    if (!draft.account_id) return verifyResult.zones;
    return verifyResult.zones.filter((z) => z.account_id === draft.account_id);
  }, [verifyResult, draft.account_id]);

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="flex-1">
          <CardTitle className="flex items-center gap-2">
            {t("settings.providers.cloudflare.title")}
            {configured ? (
              <Badge tone="success" className="gap-1">
                <CheckCircle2 className="h-3 w-3" />
                {t("settings.providers.cloudflare.badge.configured")}
              </Badge>
            ) : (
              <Badge tone="neutral">{t("settings.providers.cloudflare.badge.not_set")}</Badge>
            )}
          </CardTitle>
          <CardDescription className="mt-1.5">
            {t("settings.providers.cloudflare.description")}
          </CardDescription>
        </div>
        {configured ? (
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setConfirmDelete(true)}
            title={t("settings.providers.cloudflare.delete_title")}
          >
            <Trash2 className="h-4 w-4 text-danger" />
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        <CloudflareTokenGuide />
        <Field
          label={t("settings.providers.cloudflare.token.label")}
          hint={
            configured
              ? t("settings.providers.cloudflare.token.hint_rotate")
              : t("settings.providers.cloudflare.token.hint_new")
          }
        >
          <div className="flex gap-2">
            <Input
              type={showToken ? "text" : "password"}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="cf-..."
              autoComplete="off"
              spellCheck={false}
              disabled={!!busy}
            />
            <Button
              variant="ghost"
              size="icon"
              type="button"
              onClick={() => setShowToken((s) => !s)}
              title={showToken ? "Hide" : "Show"}
            >
              {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          </div>
        </Field>

        {/* Selectors — only meaningful after a verify run. */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field
            label={t("settings.providers.cloudflare.account.label")}
            hint={t("settings.providers.cloudflare.account.hint")}
          >
            {verifyResult && verifyResult.accounts.length > 0 ? (
              <Select
                value={draft.account_id ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, account_id: e.target.value || null }))
                }
                disabled={!!busy}
              >
                <option value="">—</option>
                {verifyResult.accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.id.slice(0, 8)}…)
                    {a.source === "zone" ? " · derived" : ""}
                  </option>
                ))}
              </Select>
            ) : (
              <Input
                value={draft.account_id ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, account_id: e.target.value || null }))
                }
                placeholder={
                  verifyResult
                    ? "Paste Account ID from dash.cloudflare.com URL"
                    : "(verify token to list accounts)"
                }
                disabled={!!busy}
              />
            )}
          </Field>

          <Field
            label={t("settings.providers.cloudflare.tunnel.label")}
            hint={t("settings.providers.cloudflare.tunnel.hint")}
          >
            {verifyResult && draft.account_id && tunnels.length > 0 ? (
              <Select
                value={draft.tunnel_id ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, tunnel_id: e.target.value || null }))
                }
                disabled={!!busy}
              >
                <option value="">—</option>
                {tunnels.map((tn) => (
                  <option key={tn.id} value={tn.id}>
                    {tn.name} ({tn.status}, {tn.connections} conns)
                  </option>
                ))}
              </Select>
            ) : (
              <Input
                value={draft.tunnel_id ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, tunnel_id: e.target.value || null }))
                }
                placeholder={
                  verifyResult && draft.account_id
                    ? "Paste Tunnel UUID (or run Inspect below to auto-detect)"
                    : "(select account first)"
                }
                disabled={!!busy}
              />
            )}
          </Field>

          <Field
            label={t("settings.providers.cloudflare.zone.label")}
            hint={t("settings.providers.cloudflare.zone.hint")}
          >
            {verifyResult ? (
              <Select
                value={draft.zone_id ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, zone_id: e.target.value || null }))
                }
                disabled={!!busy}
              >
                <option value="">—</option>
                {zonesForAccount.map((z) => (
                  <option key={z.id} value={z.id}>
                    {z.name}
                  </option>
                ))}
              </Select>
            ) : (
              <Input
                value={draft.zone_id ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, zone_id: e.target.value || null }))
                }
                placeholder="(optional)"
                disabled={!!busy}
              />
            )}
          </Field>

          <Field
            label={t("settings.providers.cloudflare.preview_domain.label")}
            hint={t("settings.providers.cloudflare.preview_domain.hint")}
          >
            <Input
              value={draft.preview_domain ?? ""}
              onChange={(e) =>
                setDraft((d) => ({ ...d, preview_domain: e.target.value || null }))
              }
              placeholder="gapt.example.com"
              disabled={!!busy}
            />
          </Field>

          <div className="sm:col-span-2">
            <Field
              label={t("settings.providers.cloudflare.upstream.label")}
              hint={t("settings.providers.cloudflare.upstream.hint")}
            >
              <Input
                value={draft.upstream ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, upstream: e.target.value || null }))
                }
                placeholder="http://localhost:38080"
                disabled={!!busy}
              />
            </Field>
          </div>
        </div>

        {err ? <p className="text-[12px] text-danger">{err}</p> : null}

        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={save} disabled={!!busy}>
            {busy === "save" ? "Saving…" : t("settings.providers.cloudflare.save")}
          </Button>
          <Button onClick={verify} disabled={!!busy || !configured} variant="ghost">
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            {busy === "verify" ? "Verifying…" : t("settings.providers.cloudflare.verify")}
          </Button>
          <Button
            onClick={fetchSnapshot}
            disabled={!!busy || !draft.account_id || !draft.tunnel_id}
            variant="ghost"
          >
            {busy === "snapshot" ? "Loading…" : t("settings.providers.cloudflare.snapshot")}
          </Button>
          <Button
            onClick={ensureWildcard}
            disabled={
              !!busy ||
              !draft.account_id ||
              !draft.tunnel_id ||
              !draft.preview_domain ||
              snapshot?.mode === "local_config"
            }
            variant="primary"
          >
            <Zap className="mr-1 h-3.5 w-3.5" />
            {busy === "wildcard"
              ? "Configuring…"
              : t("settings.providers.cloudflare.ensure_wildcard")}
          </Button>
        </div>

        {verifyResult ? (
          (() => {
            const hasWarn = verifyResult.warnings.length > 0;
            const tunnelCount = Object.values(verifyResult.tunnels_by_account).reduce(
              (n, arr) => n + arr.length,
              0,
            );
            return (
              <div
                className={
                  hasWarn
                    ? "rounded border border-warn/40 bg-warn/5 px-3 py-2 text-[11.5px] text-fg-muted"
                    : "rounded border border-success/40 bg-success/5 px-3 py-2 text-[11.5px] text-fg-muted"
                }
              >
                <p
                  className={
                    hasWarn ? "mb-1 font-medium text-warn" : "mb-1 font-medium text-success"
                  }
                >
                  {t("settings.providers.cloudflare.verified")}
                </p>
                <p>
                  Accounts: {verifyResult.accounts.length}, Zones: {verifyResult.zones.length},
                  Tunnels: {tunnelCount}
                </p>
                {hasWarn ? (
                  <ul className="mt-1.5 space-y-1 text-[11px] text-warn">
                    {verifyResult.warnings.map((w, i) => (
                      <li key={i} className="leading-relaxed">
                        ⚠ {w}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            );
          })()
        ) : null}

        {snapshot ? (
          <div className="rounded border border-border bg-bg-subtle px-3 py-2 text-[11.5px]">
            <div className="mb-1.5 flex items-center gap-1.5">
              <span className="font-medium text-fg">
                {t("settings.providers.cloudflare.tunnel_mode")}:
              </span>
              <Badge
                tone={
                  snapshot.mode === "remote_managed"
                    ? "success"
                    : snapshot.mode === "local_config"
                      ? "warn"
                      : "neutral"
                }
              >
                {snapshot.mode}
              </Badge>
            </div>
            {snapshot.mode === "local_config" ? (
              <p className="mb-2 text-warn">
                {t("settings.providers.cloudflare.local_config_warning")}
              </p>
            ) : null}
            <div className="space-y-1">
              <p className="text-fg-muted">
                {t("settings.providers.cloudflare.ingress")} ({snapshot.ingress.length}):
              </p>
              <ul className="space-y-0.5 font-mono text-[10.5px] text-fg-muted">
                {snapshot.ingress.map((e, i) => (
                  <li key={i}>
                    {e.hostname || "<catch-all>"} → {e.service}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        {resp?.verified_at ? (
          <p className="text-[11px] text-fg-subtle">
            {t("settings.providers.cloudflare.last_verified")}: {formatRelative(resp.verified_at)}
          </p>
        ) : null}

        <MigrationWizard
          configured={configured}
          accountId={draft.account_id}
          tunnelId={draft.tunnel_id}
          onTunnelDetected={(uuid) => {
            // If the form has no tunnel selected yet, fill it in
            // from the local config. Doesn't auto-save — operator
            // must still click Save to persist.
            setDraft((d) => (d.tunnel_id ? d : { ...d, tunnel_id: uuid }));
          }}
          onSnapshotChanged={(snap) => setSnapshot(snap)}
          onMigrationRecorded={() => setMigrationTick((n) => n + 1)}
        />

        <MigrationHistorySection refreshKey={migrationTick} />
      </CardContent>
      <ConfirmDialog
        open={confirmDelete}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={remove}
        title={t("settings.providers.cloudflare.delete_title")}
        description={t("settings.providers.cloudflare.delete_description")}
        confirmLabel="Delete"
        cancelLabel="Cancel"
        tone="danger"
        busy={busy === "delete"}
      />
    </Card>
  );
}

// ────────────────────────────────────────────── Workspace GPU card ──
//
// Phase E.1 — exposes the global Plan/Act-grade GPU policy for the
// `gapt-ws-<wid>` containers. Read-only display: changing the
// policy requires setting `GAPT_WORKSPACE_GPUS` and restarting the
// server. Mirrors the pattern of `GAPT_MAX_ACTIVE_SANDBOXES` —
// settings live in env vars, the UI reflects them.

function WorkspaceGpuCard() {
  const { t } = useI18n();
  const [resp, setResp] = useState<GpusResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      setResp(await getGpuInfo());
    } catch (e) {
      setErr(describeApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Translate the applied policy into a tone the user can read at a
  // glance. `null` = CPU-only — neutral. Anything else = a GPU is
  // mapped, success.
  const policyText = resp?.applied_policy ?? t("settings.gpu.policy.cpu_only");
  const policyTone: "neutral" | "success" =
    resp?.applied_policy ? "success" : "neutral";

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="flex-1">
          <CardTitle className="flex items-center gap-2">
            {t("settings.gpu.title")}
            <Badge tone={policyTone}>{policyText}</Badge>
          </CardTitle>
          <CardDescription className="mt-1.5">
            {t("settings.gpu.description")}
          </CardDescription>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={refresh}
          disabled={loading}
          title={t("settings.gpu.refresh")}
        >
          <RefreshCw
            className={loading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"}
          />
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {err ? (
          <p
            role="alert"
            className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
          >
            {err}
          </p>
        ) : null}

        {resp ? (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-md border border-border bg-bg-subtle px-3 py-2">
                <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-fg-subtle">
                  {t("settings.gpu.applied.label")}
                </p>
                <p className="font-mono text-[12px] text-fg">
                  {resp.applied_policy ?? t("settings.gpu.policy.cpu_only")}
                </p>
                <p className="mt-1 text-[11px] text-fg-muted">
                  {resp.applied_policy
                    ? t("settings.gpu.applied.hint_on")
                    : t("settings.gpu.applied.hint_off")}
                </p>
              </div>
              <div className="rounded-md border border-border bg-bg-subtle px-3 py-2">
                <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-fg-subtle">
                  {t("settings.gpu.host.label")}
                </p>
                <p className="text-[12px] text-fg">
                  {resp.available
                    ? t("settings.gpu.host.count").replace(
                        "{n}",
                        String(resp.gpus.length),
                      )
                    : t("settings.gpu.host.absent")}
                </p>
              </div>
            </div>

            {resp.available && resp.gpus.length > 0 ? (
              <ul className="space-y-1 rounded-md border border-border bg-bg-elevated px-3 py-2">
                {resp.gpus.map((g) => (
                  <li
                    key={g.index}
                    className="flex items-center justify-between gap-3 text-[11.5px]"
                  >
                    <span className="font-mono text-fg-muted">#{g.index}</span>
                    <span className="flex-1 truncate text-fg">{g.name}</span>
                    <span className="text-fg-subtle">
                      {(g.memory_total_bytes / (1024 * 1024 * 1024)).toFixed(1)}{" "}
                      GiB
                    </span>
                    <span className="text-fg-subtle">
                      {t("settings.gpu.host.driver")}: {g.driver_version}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}

            <div className="rounded-md border border-border bg-bg-subtle px-3 py-2">
              <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-fg-subtle">
                {t("settings.gpu.change.label")}
              </p>
              <p className="mb-2 text-[11.5px] text-fg-muted">
                {t("settings.gpu.change.body")}
              </p>
              <div className="flex flex-wrap items-center gap-2 font-mono text-[11.5px]">
                <code className="rounded bg-bg px-1.5 py-0.5 text-fg">
                  {resp.policy_env_var}=all
                </code>
                <code className="rounded bg-bg px-1.5 py-0.5 text-fg">
                  {resp.policy_env_var}=0
                </code>
                <code className="rounded bg-bg px-1.5 py-0.5 text-fg">
                  {resp.policy_env_var}=0,1
                </code>
                <code className="rounded bg-bg px-1.5 py-0.5 text-fg-muted">
                  {t("settings.gpu.change.unset")}
                </code>
              </div>
              <p className="mt-2 text-[11px] text-warn">
                {t("settings.gpu.change.restart_hint")}
              </p>
            </div>
          </>
        ) : (
          <p className="text-[12px] text-fg-subtle">
            {t("settings.gpu.loading")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
