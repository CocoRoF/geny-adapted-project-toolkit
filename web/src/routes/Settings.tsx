import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, ChevronLeft, Eye, EyeOff, KeyRound, Settings as SettingsIcon, Trash2, X } from "lucide-react";

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
import { Field, Input } from "@/ui/Input";

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
