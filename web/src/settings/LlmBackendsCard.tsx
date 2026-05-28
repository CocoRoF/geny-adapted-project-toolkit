/**
 * Phase G.2 — LLM backends overview card for Settings.
 *
 * 5-row grid (anthropic / openai / google / vllm / claude_code_cli)
 * with a per-provider state badge + a "Set credentials" button.
 * Clicking the Claude Code row opens the G.1 auth modal; clicking
 * any API-key row opens a tiny inline editor that pastes into the
 * vault via `storeProviderApiKey` (or deletes via DELETE).
 *
 * No multi-provider manifest switching here — that's G.5. This is
 * just *visibility + credential entry* so the user can see whether
 * a given backend is ready before relying on it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type ProviderHealth,
  type ProviderState,
  deleteProviderApiKey,
  getBackendsHealth,
  storeProviderApiKey,
} from "@/api/llm_backends";
import { useI18n } from "@/app/providers/i18n-context";
import { ClaudeCodeAuthModal } from "@/settings/ClaudeCodeAuthModal";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/Card";
import { Field, Input } from "@/ui/Input";

const STATE_TONE: Record<ProviderState, "success" | "neutral" | "warn" | "danger"> = {
  ok: "success",
  missing: "neutral",
  expired: "warn",
  unreachable: "danger",
  unknown: "neutral",
};

// Providers that the modal handles via a dedicated flow (not a
// generic API-key paste). Today: just Claude Code CLI.
const SPECIAL_MODAL_PROVIDERS = new Set(["claude_code_cli"]);

// Providers that ONLY accept an API key (no other auth shape).
const API_KEY_PROVIDERS = new Set(["anthropic", "openai", "google"]);

export function LlmBackendsCard() {
  const { t } = useI18n();
  const [rows, setRows] = useState<ProviderHealth[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null); // provider slug
  const [claudeModalOpen, setClaudeModalOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await getBackendsHealth();
      setRows(r.providers);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const claudeRow = useMemo(
    () => rows.find((r) => r.provider === "claude_code_cli") ?? null,
    [rows],
  );

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div className="flex-1">
            <CardTitle>{t("settings.llm_backends.title")}</CardTitle>
            <CardDescription className="mt-1.5">
              {t("settings.llm_backends.description")}
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={refresh}
            disabled={loading}
            title={t("settings.llm_backends.refresh")}
          >
            <RefreshCw className={loading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {error ? (
            <p role="alert" className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger">
              {error}
            </p>
          ) : null}
          {loading && rows.length === 0 ? (
            <p className="text-[12px] text-fg-subtle">{t("settings.llm_backends.loading")}</p>
          ) : null}
          <ul className="divide-y divide-border overflow-hidden rounded-md border border-border">
            {rows.map((row) => (
              <li key={row.provider}>
                <ProviderRow
                  row={row}
                  expanded={editing === row.provider}
                  onToggleEdit={() =>
                    setEditing((cur) => (cur === row.provider ? null : row.provider))
                  }
                  onOpenClaudeModal={() => setClaudeModalOpen(true)}
                  onRefresh={refresh}
                />
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <ClaudeCodeAuthModal
        open={claudeModalOpen}
        onClose={() => setClaudeModalOpen(false)}
        initialHealth={claudeRow}
        onHealthChanged={refresh}
      />
    </>
  );
}

// ──────────────────────────────────────────── row ──

interface RowProps {
  row: ProviderHealth;
  expanded: boolean;
  onToggleEdit: () => void;
  onOpenClaudeModal: () => void;
  onRefresh: () => void;
}

function ProviderRow({
  row,
  expanded,
  onToggleEdit,
  onOpenClaudeModal,
  onRefresh,
}: RowProps) {
  const { t } = useI18n();
  const isClaudeCli = row.provider === "claude_code_cli";
  const isApiKey = API_KEY_PROVIDERS.has(row.provider);

  return (
    <div className="bg-bg-elevated">
      <div className="flex items-center gap-3 px-3 py-2">
        <StateBadge state={row.state} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[12.5px] font-medium text-fg">{row.label}</p>
          <p className="truncate text-[11px] text-fg-muted" title={row.detail}>
            {row.detail}
          </p>
        </div>
        {isClaudeCli ? (
          <Button variant="secondary" size="sm" onClick={onOpenClaudeModal}>
            <KeyRound className="h-3 w-3" />
            {t("settings.llm_backends.row.configure")}
          </Button>
        ) : isApiKey ? (
          <Button variant="secondary" size="sm" onClick={onToggleEdit}>
            <KeyRound className="h-3 w-3" />
            {t(
              row.state === "ok"
                ? "settings.llm_backends.row.update"
                : "settings.llm_backends.row.set_key",
            )}
          </Button>
        ) : (
          // vLLM — env-var driven; just show the hint.
          <code className="text-[10.5px] text-fg-subtle">{row.env_var}</code>
        )}
      </div>
      {expanded && isApiKey ? (
        <ApiKeyEditor
          provider={row.provider}
          envVar={row.env_var}
          alreadyStored={row.state === "ok"}
          onSaved={() => {
            onToggleEdit();
            onRefresh();
          }}
        />
      ) : null}
    </div>
  );
}

function StateBadge({ state }: { state: ProviderState }) {
  const tone = STATE_TONE[state];
  const Icon =
    state === "ok"
      ? CheckCircle2
      : state === "expired"
        ? AlertTriangle
        : state === "unreachable"
          ? AlertCircle
          : AlertCircle;
  return (
    <Badge tone={tone} className="gap-1">
      <Icon className="h-2.5 w-2.5" />
      {state}
    </Badge>
  );
}

// ─────────────────────────────────── api-key editor ──

function ApiKeyEditor({
  provider,
  envVar,
  alreadyStored,
  onSaved,
}: {
  provider: string;
  envVar: string | null;
  alreadyStored: boolean;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    if (!value.trim()) return;
    setBusy("save");
    setErr(null);
    try {
      await storeProviderApiKey(provider, value.trim());
      setValue("");
      onSaved();
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const clear = async () => {
    if (!window.confirm(t("settings.llm_backends.editor.delete_confirm"))) return;
    setBusy("delete");
    setErr(null);
    try {
      await deleteProviderApiKey(provider);
      onSaved();
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="border-t border-border bg-bg-subtle px-3 py-2">
      <Field
        label={t("settings.llm_backends.editor.label")}
        hint={
          envVar
            ? t("settings.llm_backends.editor.hint").replace("{env}", envVar)
            : t("settings.llm_backends.editor.hint_no_env")
        }
      >
        <div className="flex gap-2">
          <Input
            type={showSecret ? "text" : "password"}
            value={value}
            onChange={(e) => setValue(e.currentTarget.value)}
            placeholder={alreadyStored ? "•••• (paste a new value to rotate)" : ""}
            autoComplete="off"
            spellCheck={false}
            disabled={!!busy}
            onKeyDown={(e) => {
              if (e.key === "Enter" && value.trim() && !busy) {
                e.preventDefault();
                void save();
              }
            }}
          />
          <Button
            variant="ghost"
            size="icon"
            type="button"
            onClick={() => setShowSecret((s) => !s)}
          >
            {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </Button>
          <Button onClick={save} disabled={!value.trim() || !!busy} variant="primary">
            {busy === "save" ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            {t("settings.llm_backends.editor.save")}
          </Button>
          {alreadyStored ? (
            <Button onClick={clear} variant="ghost" size="sm" disabled={!!busy}>
              {busy === "delete" ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Trash2 className="h-3 w-3 text-danger" />
              )}
            </Button>
          ) : null}
        </div>
      </Field>
      {err ? <p className="mt-1.5 text-[11px] text-danger">{err}</p> : null}
    </div>
  );
}

function describeError(e: unknown): string {
  if (e instanceof ApiError) return `${e.code}: ${e.reason}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
