import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bot, CheckCircle2, ChevronLeft, Eye, EyeOff, KeyRound, Settings as SettingsIcon, Trash2, X } from "lucide-react";

import { type AgentPrefs, getAgentPrefs, putAgentPrefs } from "@/api/agent_prefs";
import { ApiError } from "@/api/client";
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

export function Settings() {
  const { t } = useI18n();
  const { me } = useAuth();
  const userId = me?.user_id ?? null;
  const [secrets, setSecrets] = useState<SecretView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!userId) {
      setSecrets([]);
      setLoading(false);
      return;
    }
    setError(null);
    try {
      const items = await listSecrets({ scope: "user", owner_id: userId });
      setSecrets(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [userId]);

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
          <CardDescription>Identity from the magic-link login.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-[13px]">
          <div className="flex justify-between gap-3">
            <span className="text-fg-muted">Email</span>
            <span className="font-mono text-fg">{me?.email ?? "—"}</span>
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
              userId={userId}
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
  userId,
  onChanged,
}: {
  spec: SecretSpec;
  existing: SecretView | null;
  userId: string | null;
  onChanged: () => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const configured = existing !== null;

  const save = async () => {
    if (!userId || value.trim() === "") return;
    setBusy(true);
    setErr(null);
    try {
      if (existing) {
        await rotateSecret(existing.id, value.trim());
      } else {
        await storeSecret({
          scope: "user",
          owner_id: userId,
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
          <Button onClick={save} disabled={busy || !userId || value.trim() === ""}>
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
