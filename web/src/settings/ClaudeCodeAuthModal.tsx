/**
 * Phase G.1.c — Claude Code (CLI) auth modal.
 *
 * Adapted from Geny's `ClaudeCodeAuthModal` but written against
 * GAPT's UI primitives (`Modal` / `Button` / `Field` / `Input`)
 * and the `/llm-backends/*` router from G.1.a.
 *
 * Four mutually-exclusive auth modes:
 *
 *   - `host_mount` — informational. GAPT already mounts the host's
 *     `~/.claude` into workspace containers. The user just verifies
 *     the host already has an active subscription via "Recheck".
 *
 *   - `device_login` — POST start, open SSE, render every stdout/
 *     stderr line live. Extract the device-code URL the CLI prints
 *     and surface it as a Copy / Open button. After visiting,
 *     paste the auth code into the input below.
 *
 *   - `setup_token` — long-lived `claude setup-token` value. Saved
 *     into the vault under the existing `anthropic_api_key` key so
 *     the executor exports it as `ANTHROPIC_API_KEY` per workspace.
 *
 *   - `api_key` — Anthropic Console API key. Same storage path as
 *     setup_token (both end up in the same env var the CLI reads).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Copy,
  ExternalLink,
  Eye,
  EyeOff,
  Loader2,
  LogIn,
  LogOut,
  RefreshCw,
  Terminal,
  X,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type AuthJobEvent,
  type AuthStatusResponse,
  type ProviderHealth,
  authJobEventsUrl,
  cancelAuthJob,
  claudeAuthLogout,
  getClaudeAuthStatus,
  recheckClaudeCode,
  startClaudeAuthLogin,
  storeClaudeSetupToken,
  storeProviderApiKey,
  submitAuthJobInput,
  testClaudeConnection,
} from "@/api/llm_backends";
import { useI18n } from "@/app/providers/i18n-context";

type TFunc = ReturnType<typeof useI18n>["t"];
import { Button } from "@/ui/Button";
import { Field, Input } from "@/ui/Input";
import { Modal } from "@/ui/Modal";

type AuthMode = "host_mount" | "device_login" | "setup_token" | "api_key";

const AUTH_MODES: AuthMode[] = ["host_mount", "device_login", "setup_token", "api_key"];
const STORAGE_KEY = "gapt.claude_auth_mode";

function readPersistedMode(): AuthMode {
  if (typeof window === "undefined") return "host_mount";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw && (AUTH_MODES as string[]).includes(raw)) return raw as AuthMode;
  } catch {
    /* private mode / quota — fall through */
  }
  return "host_mount";
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** Optional: caller can prime the modal with the latest health
   *  card so the status badge renders instantly without a round-trip. */
  initialHealth?: ProviderHealth | null;
  /** Fired when the modal believes the auth state changed
   *  (login finished, logout, token saved). Caller refreshes its
   *  `getBackendsHealth()` view. */
  onHealthChanged?: () => void;
}

export function ClaudeCodeAuthModal({
  open,
  onClose,
  initialHealth = null,
  onHealthChanged,
}: Props) {
  const { t } = useI18n();
  const [mode, setMode] = useState<AuthMode>(() => readPersistedMode());
  const [status, setStatus] = useState<AuthStatusResponse | null>(null);
  // Health is recheck-and-forget here — the card outside owns the
  // displayed value; we only need the setter to keep it fresh.
  const [, setHealth] = useState<ProviderHealth | null>(initialHealth);
  const [statusLoading, setStatusLoading] = useState(false);
  const [flash, setFlash] = useState<{ kind: "ok" | "warn" | "err"; text: string } | null>(null);
  const [job, setJob] = useState<{ id: string; events: AuthJobEvent[] } | null>(null);
  const [authCodeInput, setAuthCodeInput] = useState("");
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState("");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [copiedUrl, setCopiedUrl] = useState(false);
  const sseRef = useRef<EventSource | null>(null);

  // Persist the radio selection so re-opening the modal keeps the
  // operator's chosen mode.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      /* best-effort */
    }
  }, [mode]);

  // Refetch `claude auth status` whenever the modal opens. Errors
  // are surfaced inline; don't block the rest of the modal.
  const refreshStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const s = await getClaudeAuthStatus();
      setStatus(s);
    } catch (e) {
      setFlash({ kind: "warn", text: describeError(e) });
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setFlash(null);
    void refreshStatus();
  }, [open, refreshStatus]);

  // SSE cleanup on unmount / close.
  useEffect(() => {
    return () => {
      sseRef.current?.close();
      sseRef.current = null;
    };
  }, []);

  // Extract the device-code URL from the streamed lines. The CLI
  // prints something like `Visit: https://...` — we accept anything
  // with `https://` in stdout and treat the first occurrence as the
  // canonical URL.
  const deviceUrl = useMemo<string | null>(() => {
    if (!job) return null;
    for (const ev of job.events) {
      if (ev.channel !== "stdout") continue;
      const match = ev.text.match(/(https?:\/\/[^\s)]+)/);
      if (match) return match[1] ?? null;
    }
    return null;
  }, [job]);

  // Auth job done when an `exit` event has arrived.
  const jobFinished = useMemo<boolean>(() => {
    if (!job) return false;
    return job.events.some((e) => e.channel === "exit");
  }, [job]);

  const closeSse = useCallback(() => {
    sseRef.current?.close();
    sseRef.current = null;
  }, []);

  const startLogin = useCallback(
    async (useConsole: boolean) => {
      setSubmitting("login");
      setFlash(null);
      try {
        const r = await startClaudeAuthLogin({ use_console: useConsole });
        const next = { id: r.job_id, events: [] as AuthJobEvent[] };
        setJob(next);
        closeSse();
        const url = authJobEventsUrl(r.job_id);
        const es = new EventSource(url, { withCredentials: true });
        sseRef.current = es;
        es.onmessage = (ev: MessageEvent) => {
          try {
            const payload = JSON.parse(ev.data as string) as AuthJobEvent;
            setJob((cur) => (cur ? { ...cur, events: [...cur.events, payload] } : cur));
            if (payload.channel === "exit") {
              closeSse();
              void refreshStatus();
              onHealthChanged?.();
            }
          } catch {
            /* ignore non-JSON heartbeat */
          }
        };
        es.onerror = () => {
          // The stream closes naturally after `exit`; only warn
          // when the close was unexpected.
          if (!jobFinished) {
            setFlash({
              kind: "warn",
              text: t("claude_auth.modal.sse_lost"),
            });
          }
          closeSse();
        };
      } catch (e) {
        setFlash({ kind: "err", text: describeError(e) });
      } finally {
        setSubmitting(null);
      }
    },
    [closeSse, jobFinished, onHealthChanged, refreshStatus, t],
  );

  const submitAuthCode = useCallback(async () => {
    if (!job) return;
    const code = authCodeInput.trim();
    if (!code) return;
    setSubmitting("authcode");
    try {
      await submitAuthJobInput(job.id, code);
      setAuthCodeInput("");
    } catch (e) {
      setFlash({ kind: "err", text: describeError(e) });
    } finally {
      setSubmitting(null);
    }
  }, [authCodeInput, job]);

  const cancelJob = useCallback(async () => {
    if (!job) return;
    try {
      await cancelAuthJob(job.id);
    } catch {
      /* swallow — job is already gone */
    }
    closeSse();
    setJob(null);
  }, [closeSse, job]);

  const onSaveSetupToken = useCallback(async () => {
    if (!tokenInput.trim()) return;
    setSubmitting("save_token");
    try {
      await storeClaudeSetupToken(tokenInput.trim());
      setTokenInput("");
      setFlash({ kind: "ok", text: t("claude_auth.modal.saved") });
      await recheckClaudeCode()
        .then(setHealth)
        .catch(() => undefined);
      onHealthChanged?.();
    } catch (e) {
      setFlash({ kind: "err", text: describeError(e) });
    } finally {
      setSubmitting(null);
    }
  }, [onHealthChanged, t, tokenInput]);

  const onSaveApiKey = useCallback(async () => {
    if (!apiKeyInput.trim()) return;
    setSubmitting("save_apikey");
    try {
      await storeProviderApiKey("anthropic", apiKeyInput.trim());
      setApiKeyInput("");
      setFlash({ kind: "ok", text: t("claude_auth.modal.saved") });
      await recheckClaudeCode()
        .then(setHealth)
        .catch(() => undefined);
      onHealthChanged?.();
    } catch (e) {
      setFlash({ kind: "err", text: describeError(e) });
    } finally {
      setSubmitting(null);
    }
  }, [apiKeyInput, onHealthChanged, t]);

  const onLogout = useCallback(async () => {
    if (!window.confirm(t("claude_auth.modal.logout_confirm"))) return;
    setSubmitting("logout");
    try {
      await claudeAuthLogout();
      await refreshStatus();
      onHealthChanged?.();
    } catch (e) {
      setFlash({ kind: "err", text: describeError(e) });
    } finally {
      setSubmitting(null);
    }
  }, [onHealthChanged, refreshStatus, t]);

  const onTest = useCallback(async () => {
    setSubmitting("test");
    try {
      const r = await testClaudeConnection();
      setFlash({
        kind: r.ok ? "ok" : "warn",
        text: r.ok ? `${t("claude_auth.modal.test_ok")} (${r.duration_ms}ms)` : r.detail,
      });
    } catch (e) {
      setFlash({ kind: "err", text: describeError(e) });
    } finally {
      setSubmitting(null);
    }
  }, [t]);

  const copyUrl = useCallback(() => {
    if (!deviceUrl) return;
    void navigator.clipboard.writeText(deviceUrl).then(() => {
      setCopiedUrl(true);
      window.setTimeout(() => setCopiedUrl(false), 1500);
    });
  }, [deviceUrl]);

  return (
    <Modal
      open={open}
      onClose={() => {
        closeSse();
        onClose();
      }}
      title={t("claude_auth.modal.title")}
      size="lg"
    >
      <div className="space-y-4">
        {/* ── Status row ───────────────────────────────────── */}
        <div className="flex items-center justify-between rounded-md border border-border bg-bg-subtle px-3 py-2 text-[12px]">
          <StatusBadge status={status} loading={statusLoading} t={t} />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void refreshStatus()}
            disabled={statusLoading}
            title={t("claude_auth.modal.refresh")}
          >
            <RefreshCw className={statusLoading ? "h-3 w-3 animate-spin" : "h-3 w-3"} />
          </Button>
        </div>

        {/* ── Mode picker ──────────────────────────────────── */}
        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
            {t("claude_auth.modal.mode.heading")}
          </p>
          <ul className="space-y-1.5">
            {AUTH_MODES.map((m) => (
              <li key={m}>
                <label className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 hover:bg-bg-subtle">
                  <input
                    type="radio"
                    name="claude_auth_mode"
                    checked={mode === m}
                    onChange={() => setMode(m)}
                    className="mt-0.5 accent-accent"
                  />
                  <span className="flex-1">
                    <span className="block text-[12.5px] font-medium text-fg">
                      {t(`claude_auth.modal.mode.${m}.label`)}
                    </span>
                    <span className="block text-[11px] text-fg-muted">
                      {t(`claude_auth.modal.mode.${m}.blurb`)}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>

        {/* ── Per-mode body ────────────────────────────────── */}
        {mode === "host_mount" ? (
          <div className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-[12px] text-fg-muted">
            {t("claude_auth.modal.host_mount.body")}
          </div>
        ) : null}

        {mode === "device_login" ? (
          <DeviceLoginBody
            t={t}
            job={job}
            jobFinished={jobFinished}
            deviceUrl={deviceUrl}
            copiedUrl={copiedUrl}
            onCopy={copyUrl}
            authCodeInput={authCodeInput}
            setAuthCodeInput={setAuthCodeInput}
            submitting={submitting}
            onStart={(useConsole) => void startLogin(useConsole)}
            onSubmitCode={() => void submitAuthCode()}
            onCancel={() => void cancelJob()}
          />
        ) : null}

        {mode === "setup_token" ? (
          <SecretInputBody
            label={t("claude_auth.modal.setup_token.label")}
            hint={t("claude_auth.modal.setup_token.hint")}
            placeholder="sk-ant-…"
            value={tokenInput}
            onChange={setTokenInput}
            showSecret={showSecret}
            onToggleShow={() => setShowSecret((s) => !s)}
            onSave={() => void onSaveSetupToken()}
            saving={submitting === "save_token"}
            saveLabel={t("claude_auth.modal.setup_token.save")}
          />
        ) : null}

        {mode === "api_key" ? (
          <SecretInputBody
            label={t("claude_auth.modal.api_key.label")}
            hint={t("claude_auth.modal.api_key.hint")}
            placeholder="sk-ant-api03-…"
            value={apiKeyInput}
            onChange={setApiKeyInput}
            showSecret={showSecret}
            onToggleShow={() => setShowSecret((s) => !s)}
            onSave={() => void onSaveApiKey()}
            saving={submitting === "save_apikey"}
            saveLabel={t("claude_auth.modal.api_key.save")}
          />
        ) : null}

        {/* ── Flash ───────────────────────────────────────── */}
        {flash ? (
          <div
            role="status"
            className={
              flash.kind === "ok"
                ? "flex items-start gap-2 rounded-md border border-success/40 bg-success/10 px-3 py-2 text-[12px] text-success"
                : flash.kind === "warn"
                  ? "flex items-start gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-[12px] text-warn"
                  : "flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
            }
          >
            {flash.kind === "ok" ? (
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            )}
            <span className="leading-relaxed">{flash.text}</span>
          </div>
        ) : null}

        {/* ── Footer actions ──────────────────────────────── */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
          <div className="flex items-center gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void onTest()}
              disabled={!!submitting}
              title={t("claude_auth.modal.test_hint")}
            >
              {submitting === "test" ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Terminal className="h-3 w-3" />
              )}
              {t("claude_auth.modal.test")}
            </Button>
            {status?.logged_in ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void onLogout()}
                disabled={!!submitting}
                className="text-danger hover:bg-danger/10"
              >
                {submitting === "logout" ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <LogOut className="h-3 w-3" />
                )}
                {t("claude_auth.modal.logout")}
              </Button>
            ) : null}
          </div>
          <Button
            variant="secondary"
            onClick={() => {
              closeSse();
              onClose();
            }}
          >
            {t("claude_auth.modal.close")}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ────────────────────────────────────── status badge ──

function StatusBadge({
  status,
  loading,
  t,
}: {
  status: AuthStatusResponse | null;
  loading: boolean;
  t: TFunc;
}) {
  if (loading || !status) {
    return (
      <span className="inline-flex items-center gap-1.5 text-fg-subtle">
        <Loader2 className="h-3 w-3 animate-spin" />
        {t("claude_auth.modal.loading")}
      </span>
    );
  }
  if (!status.logged_in) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-danger/40 bg-danger/10 px-2 py-0.5 text-[11px] text-danger">
        <AlertCircle className="h-3 w-3" />
        {t("claude_auth.modal.not_authed")}
      </span>
    );
  }
  const sub = (status.subscription_type ?? "").toLowerCase();
  const label = sub
    ? sub.charAt(0).toUpperCase() + sub.slice(1) + " plan"
    : status.auth_method === "api_key"
      ? t("claude_auth.modal.api_key.label")
      : t("claude_auth.modal.logged_in");
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-success/40 bg-success/10 px-2 py-0.5 text-[11px] text-success">
      <CheckCircle2 className="h-3 w-3" />
      {label}
      {status.email ? <span className="ml-1 text-fg-muted">· {status.email}</span> : null}
    </span>
  );
}

// ───────────────────────────────── device-login body ──

function DeviceLoginBody({
  t,
  job,
  jobFinished,
  deviceUrl,
  copiedUrl,
  onCopy,
  authCodeInput,
  setAuthCodeInput,
  submitting,
  onStart,
  onSubmitCode,
  onCancel,
}: {
  t: TFunc;
  job: { id: string; events: AuthJobEvent[] } | null;
  jobFinished: boolean;
  deviceUrl: string | null;
  copiedUrl: boolean;
  onCopy: () => void;
  authCodeInput: string;
  setAuthCodeInput: (v: string) => void;
  submitting: string | null;
  onStart: (useConsole: boolean) => void;
  onCancel: () => void;
  onSubmitCode: () => void;
}) {
  if (!job) {
    return (
      <div className="space-y-2">
        <p className="text-[11.5px] text-fg-muted">
          {t("claude_auth.modal.device_login.preamble")}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            onClick={() => onStart(false)}
            disabled={submitting === "login"}
          >
            {submitting === "login" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <LogIn className="h-3 w-3" />
            )}
            {t("claude_auth.modal.device_login.start_subscription")}
          </Button>
          <Button
            variant="secondary"
            onClick={() => onStart(true)}
            disabled={submitting === "login"}
          >
            {t("claude_auth.modal.device_login.start_console")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {deviceUrl ? (
        <div className="rounded-md border border-accent/40 bg-accent/5 px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-accent">
            {t("claude_auth.modal.device_login.visit_url")}
          </p>
          <div className="mt-1 flex items-center gap-2">
            <code className="flex-1 truncate font-mono text-[11.5px] text-fg">{deviceUrl}</code>
            <Button variant="ghost" size="sm" onClick={onCopy} title="Copy">
              {copiedUrl ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
            </Button>
            <a
              href={deviceUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-surface px-2.5 text-[11px] font-medium text-fg-muted hover:bg-surface-hover hover:text-fg"
            >
              <ExternalLink className="h-3 w-3" />
              {t("claude_auth.modal.device_login.open")}
            </a>
          </div>
        </div>
      ) : null}

      {!jobFinished ? (
        <Field
          label={t("claude_auth.modal.device_login.auth_code_label")}
          hint={t("claude_auth.modal.device_login.auth_code_hint")}
        >
          <div className="flex gap-2">
            <Input
              type="text"
              value={authCodeInput}
              onChange={(e) => setAuthCodeInput(e.currentTarget.value)}
              placeholder="paste-code-here"
              spellCheck={false}
              onKeyDown={(e) => {
                if (e.key === "Enter" && authCodeInput.trim()) {
                  e.preventDefault();
                  onSubmitCode();
                }
              }}
            />
            <Button
              onClick={onSubmitCode}
              disabled={!authCodeInput.trim() || submitting === "authcode"}
            >
              {submitting === "authcode" ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
              {t("claude_auth.modal.device_login.submit_code")}
            </Button>
          </div>
        </Field>
      ) : null}

      {/* Live console */}
      <details className="rounded-md border border-border bg-bg" open>
        <summary className="cursor-pointer px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
          {t("claude_auth.modal.device_login.console")}
        </summary>
        <pre className="max-h-48 overflow-auto px-3 py-2 font-mono text-[10.5px] leading-snug text-fg">
          {job.events.map((ev, i) => (
            <span
              key={i}
              className={
                ev.channel === "stderr"
                  ? "block text-warn"
                  : ev.channel === "exit"
                    ? "block text-fg-subtle"
                    : ev.channel === "stdin"
                      ? "block text-accent"
                      : "block"
              }
            >
              {ev.text || " "}
            </span>
          ))}
        </pre>
      </details>

      <div className="flex items-center justify-end gap-2">
        {!jobFinished ? (
          <Button variant="ghost" size="sm" onClick={onCancel}>
            <X className="h-3 w-3" />
            {t("claude_auth.modal.device_login.cancel")}
          </Button>
        ) : (
          <span className="text-[11px] text-fg-subtle">
            {t("claude_auth.modal.device_login.finished")}
          </span>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────── secret-paste body ──

function SecretInputBody({
  label,
  hint,
  placeholder,
  value,
  onChange,
  showSecret,
  onToggleShow,
  onSave,
  saving,
  saveLabel,
}: {
  label: string;
  hint: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  showSecret: boolean;
  onToggleShow: () => void;
  onSave: () => void;
  saving: boolean;
  saveLabel: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <div className="flex gap-2">
        <Input
          type={showSecret ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.currentTarget.value)}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
        />
        <Button
          variant="ghost"
          size="icon"
          type="button"
          onClick={onToggleShow}
          title={showSecret ? "Hide" : "Show"}
        >
          {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </Button>
        <Button onClick={onSave} disabled={!value.trim() || saving}>
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
          {saveLabel}
        </Button>
      </div>
    </Field>
  );
}

// ───────────────────────────────────────── helpers ──

function describeError(e: unknown): string {
  if (e instanceof ApiError) return `${e.code}: ${e.reason}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
