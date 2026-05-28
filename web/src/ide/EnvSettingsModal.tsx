/** Edit-mode environment settings (Deploy view → env card → ⚙).
 *
 * Phase H rewrite — form fields, presets, kind dispatch, and
 * field-level error rendering all delegate to the unified
 * `EnvironmentEditor` (web/src/environments/EnvironmentEditor.tsx).
 *
 * What stays in this file:
 *   - The modal chrome (title, footer Save / Save & re-route /
 *     Help / Close buttons).
 *   - The 422 `fields[]` error capture for inline display in the
 *     editor.
 *   - The subdomain-mode setup guide (`SubdomainSetupGuide`) +
 *     its supporting `Step`, `CheckLine`, `CallToActionRow`, and
 *     `OpenCertGuideButton` helpers — these are deploy-view-specific
 *     diagnostics, not part of the create/edit form contract.
 *   - `Save & re-route` — pulls fields out of the editor's FormState
 *     and forwards them to `rerouteStack` so a running stack picks
 *     up the new routing without a full re-deploy.
 *
 * What got deleted vs the pre-Phase-H version:
 *   - Local `FormState` / `PRESETS` / `readForm` / `writeConfig`
 *     (now in EnvironmentEditor).
 *   - Local `Section` / `Field` / `Input` / `Select` / `Toggle` /
 *     `ModeButton` atoms (duplicated; EnvironmentEditor has its own).
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Check,
  CircleAlert,
  Copy,
  ExternalLink,
  HelpCircle,
  Loader2,
  Settings as SettingsIcon,
  Zap,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type EnvironmentResponse,
  type SubdomainDiagnose,
  diagnoseSubdomainMode,
  rerouteStack,
  updateEnvironment,
} from "@/api/environments";
import { ensureCloudflareWildcard } from "@/api/providers";
import { useI18n } from "@/app/providers/i18n-context";
import {
  EnvironmentEditor,
  type FieldError,
  type FormState,
  readForm,
  writeForm,
} from "@/environments/EnvironmentEditor";
import { StackRerouteHelpModal } from "@/ide/StackRerouteHelpModal";
import { WildcardCertGuide } from "@/ide/WildcardCertGuide";
import { Button } from "@/ui/Button";
import { Modal } from "@/ui/Modal";

interface Props {
  open: boolean;
  env: EnvironmentResponse;
  onClose: () => void;
  onSaved: (updated: EnvironmentResponse) => void;
}

export function EnvSettingsModal({ open, env, onClose, onSaved }: Props) {
  const { t } = useI18n();
  const [form, setForm] = useState<FormState>(() => readForm(env));
  const [saving, setSaving] = useState<"save" | "save_reroute" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldError[]>([]);
  const [helpOpen, setHelpOpen] = useState(false);
  // Bumped on save / reroute so the `SubdomainSetupGuide` re-runs
  // its diagnose. Without this the operator sees stale "needs cert"
  // checks long after they fixed the underlying issue.
  const [saveTick, setSaveTick] = useState(0);

  // Refresh form when the modal opens for a different env. Without
  // this, the saved form sticks around when the operator hops
  // between envs without closing the modal.
  useEffect(() => {
    setForm(readForm(env));
    setFieldErrors([]);
    setErr(null);
    setFlash(null);
  }, [env]);

  const save = async (alsoReroute: boolean) => {
    setSaving(alsoReroute ? "save_reroute" : "save");
    setErr(null);
    setFlash(null);
    setFieldErrors([]);
    try {
      const payload = writeForm(form);
      const updated = await updateEnvironment(env.id, payload);
      onSaved(updated);
      if (alsoReroute) {
        // Pull the routing-relevant slice out of the form. Only fields
        // the running stack would care about — leaves preview_slug /
        // build / cost_multiplier alone.
        const r = await rerouteStack(env.id, {
          preview_mode:
            form.preview_mode === "" ? null : form.preview_mode,
          primary_service: form.primary_service.trim() || null,
          primary_port: Number.parseInt(form.primary_port, 10) || null,
          strip_prefix:
            form.strip_prefix === "" ? null : form.strip_prefix === "true",
          upstream_scheme: form.upstream_scheme || null,
          upstream_host_header: form.upstream_host_header.trim() || null,
          upstream_tls_insecure: form.upstream_tls_insecure,
        });
        if (r.ok) {
          setFlash(t("env_settings.saved_and_rerouted"));
        } else {
          setErr(
            t("env_settings.reroute_failed") + "\n" + r.output.slice(-300),
          );
        }
      } else {
        setFlash(t("env_settings.saved"));
      }
      setSaveTick((n) => n + 1);
    } catch (e) {
      if (e instanceof ApiError) {
        // 422 + `fields[]` from H.1's validator → highlight the
        // exact field that's wrong inline in the editor.
        const fields = (e.detail as { fields?: FieldError[] } | undefined)?.fields;
        if (Array.isArray(fields) && fields.length > 0) {
          setFieldErrors(fields);
          setErr(
            fields.map((f) => `${f.loc.join(".")}: ${f.msg}`).join("; "),
          );
        } else {
          setErr(e.reason);
        }
      } else {
        setErr(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setSaving(null);
    }
  };

  const showSubdomainGuide =
    form.kind === "local" && form.preview_mode === "subdomain";

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="xl"
      title={`${t("env_settings.title")} — ${env.name}`}
      description={t("env_settings.subtitle")}
      footer={
        <>
          <Button
            variant="ghost"
            onClick={() => setHelpOpen(true)}
            disabled={saving !== null}
            title={t("env_settings.help_title")}
            className="mr-auto"
          >
            <HelpCircle className="mr-1 h-3 w-3" />
            {t("env_settings.help")}
          </Button>
          <Button variant="ghost" onClick={onClose} disabled={saving !== null}>
            {t("env_settings.close")}
          </Button>
          <Button
            variant="ghost"
            onClick={() => void save(false)}
            disabled={saving !== null}
          >
            {saving === "save" ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : null}
            {t("env_settings.save")}
          </Button>
          <Button
            variant="primary"
            onClick={() => void save(true)}
            disabled={saving !== null}
            title={t("env_settings.save_and_reroute_title")}
          >
            {saving === "save_reroute" ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : null}
            {t("env_settings.save_and_reroute")}
          </Button>
        </>
      }
    >
      <div className="max-h-[70vh] space-y-4 overflow-auto pr-1">
        <EnvironmentEditor
          mode="edit"
          projectId={env.project_id}
          form={form}
          onFormChange={setForm}
          fieldErrors={fieldErrors}
          disabled={saving !== null}
          extraBelowKindSection={
            showSubdomainGuide ? (
              <SubdomainSetupGuide refreshKey={saveTick} />
            ) : null
          }
        />

        {err ? (
          <pre
            role="alert"
            className="whitespace-pre-wrap break-words rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[11.5px] text-danger"
          >
            {err}
          </pre>
        ) : null}
        {flash ? (
          <p className="rounded-md border border-success/40 bg-success/10 px-3 py-1.5 text-[11.5px] text-success">
            {flash}
          </p>
        ) : null}
      </div>
      <StackRerouteHelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </Modal>
  );
}

// ─────────────────────────────────── subdomain setup guide ──

/** Step-by-step subdomain-mode prerequisites + live diagnose.
 *
 * Surfaces the THREE things the operator has to get right for
 * subdomain mode to work end-to-end:
 *   1. Wildcard DNS pointing the `*.<preview-domain>` at the GAPT
 *      edge (Caddy / Cloudflare Tunnel).
 *   2. Cloudflare Tunnel ingress including the wildcard (if using
 *      cloudflared).
 *   3. GAPT_CADDY_PREVIEW_DOMAIN env var set on the server.
 */
function SubdomainSetupGuide({ refreshKey = 0 }: { refreshKey?: number }) {
  const { t } = useI18n();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<SubdomainDiagnose | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const diagnose = useCallback(async () => {
    setRunning(true);
    setErr(null);
    try {
      const r = await diagnoseSubdomainMode();
      setResult(r);
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.reason
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setRunning(false);
    }
  }, []);

  useEffect(() => {
    void diagnose();
  }, [diagnose, refreshKey]);

  const providerHandled =
    !!result &&
    result.provider_configured &&
    result.tunnel_mode === "remote_managed" &&
    result.tunnel_has_wildcard;

  const domain = result?.preview_domain ?? "<your-preview-domain>";
  const cnameSnippet = `Type:    CNAME
Name:    *
Content: ${domain}.cdn.cloudflare.net   ← 또는 기존 ${domain} 의 target 과 동일
TTL:     Auto
Proxy:   ✓ (orange cloud)`;
  const tunnelSnippet = `# ~/.cloudflared/config.yml — ingress 에 와일드카드 항목 추가
ingress:
  - hostname: "*.${domain}"
    service: http://localhost:38080
  - hostname: ${domain}
    service: http://localhost:38080
  - service: http_status:404`;

  return (
    <GuideSection
      title={
        providerHandled
          ? t("env_settings.section.subdomain_setup_ready")
          : t("env_settings.section.subdomain_setup")
      }
      hint={
        providerHandled
          ? t("env_settings.section.subdomain_setup_ready_hint")
          : t("env_settings.section.subdomain_setup_hint")
      }
    >
      {providerHandled ? (
        <div className="rounded-md border border-success/40 bg-success/5 px-3 py-2.5">
          <p className="mb-1.5 inline-flex items-center gap-1.5 text-[12px] font-medium text-success">
            <Check className="h-3.5 w-3.5" strokeWidth={2.5} />
            {t("env_settings.subdomain.ready.title")}
          </p>
          <ul className="space-y-0.5 text-[11.5px] leading-relaxed text-fg-muted">
            <li>• {t("env_settings.subdomain.ready.dns")}</li>
            <li>• {t("env_settings.subdomain.ready.tunnel")}</li>
            <li>• {t("env_settings.subdomain.ready.env")}</li>
          </ul>
          {!result.e2e_reachable ? (
            <p className="mt-1.5 inline-flex items-start gap-1 text-[11px] text-warn">
              <CircleAlert className="mt-0.5 h-3 w-3 shrink-0" />
              <span>{t("env_settings.subdomain.ready.cert_warning")}</span>
            </p>
          ) : null}
        </div>
      ) : (
        <>
          <Step
            n={1}
            title={t("env_settings.subdomain.step1.title")}
            body={t("env_settings.subdomain.step1.body")}
            snippet={cnameSnippet}
          />
          <Step
            n={2}
            title={t("env_settings.subdomain.step2.title")}
            body={t("env_settings.subdomain.step2.body")}
            snippet={tunnelSnippet}
          />
          <Step
            n={3}
            title={t("env_settings.subdomain.step3.title")}
            body={t("env_settings.subdomain.step3.body")}
            snippet="# GAPT 서버 환경변수
GAPT_CADDY_PREVIEW_DOMAIN=gapt.hrletsgo.me
GAPT_CADDY_ADMIN_URL=http://127.0.0.1:32019"
          />
        </>
      )}

      <div className="mt-3 rounded-md border border-border bg-bg p-2.5">
        <header className="mb-2 flex items-center gap-2">
          <span className="text-[11.5px] font-semibold text-fg">
            {t("env_settings.subdomain.diagnose.title")}
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void diagnose()}
            disabled={running}
            className="ml-auto"
          >
            {running ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : null}
            {t("env_settings.subdomain.diagnose.run")}
          </Button>
        </header>
        {err ? <p className="text-[11px] text-danger">{err}</p> : null}
        {result ? (
          <div className="space-y-1.5">
            <CheckLine
              label={t("env_settings.subdomain.check.env")}
              ok={!!result.preview_domain}
              detail={result.preview_domain ?? "—"}
            />
            <CheckLine
              label={t("env_settings.subdomain.check.dns").replace(
                "{host}",
                result.sample_host,
              )}
              ok={result.dns_resolves}
              detail={result.dns_message}
            />
            <CheckLine
              label={t("env_settings.subdomain.check.caddy_admin")}
              ok={result.caddy_admin_reachable}
              detail={
                result.caddy_admin_reachable
                  ? t("env_settings.subdomain.check.ok")
                  : t("env_settings.subdomain.check.fail")
              }
            />
            <CheckLine
              label={t("env_settings.subdomain.check.caddy_wildcard")}
              ok={result.caddy_has_wildcard_server}
              detail={
                result.caddy_has_wildcard_server
                  ? t("env_settings.subdomain.check.ok")
                  : t("env_settings.subdomain.check.fail")
              }
            />
            <CheckLine
              label={t("env_settings.subdomain.check.e2e").replace(
                "{host}",
                result.sample_host,
              )}
              ok={result.e2e_reachable}
              detail={result.e2e_message || t("env_settings.subdomain.check.fail")}
            />
            <CheckLine
              label={t("env_settings.subdomain.check.provider")}
              ok={result.provider_configured}
              detail={
                result.provider_configured
                  ? t("env_settings.subdomain.check.provider_configured")
                  : t("env_settings.subdomain.check.provider_not_set")
              }
            />
            {result.provider_configured ? (
              <CheckLine
                label={t("env_settings.subdomain.check.tunnel_mode")}
                ok={result.tunnel_mode === "remote_managed"}
                detail={result.tunnel_mode ?? "—"}
              />
            ) : null}
            {result.provider_configured && result.tunnel_mode === "remote_managed" ? (
              <CheckLine
                label={t("env_settings.subdomain.check.tunnel_wildcard")}
                ok={result.tunnel_has_wildcard}
                detail={
                  result.tunnel_has_wildcard
                    ? t("env_settings.subdomain.check.ok")
                    : t("env_settings.subdomain.check.fail")
                }
              />
            ) : null}
            <CallToActionRow
              diagnose={result}
              onWildcardConfigured={diagnose}
            />
            {result.next_steps.length > 0 ? (
              <div className="mt-2 rounded border border-warn/40 bg-warn/5 px-2 py-1.5">
                <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-warn">
                  {t("env_settings.subdomain.next_steps")}
                </p>
                <ul className="space-y-1 text-[11px] text-fg-muted">
                  {result.next_steps.map((s, i) => (
                    <li key={i} className="leading-relaxed">
                      • {s}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="text-[11px] text-fg-subtle">
            {t("env_settings.subdomain.diagnose.idle")}
          </p>
        )}
      </div>
    </GuideSection>
  );
}

function Step({
  n,
  title,
  body,
  snippet,
}: {
  n: number;
  title: string;
  body: string;
  snippet: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-md border border-border bg-bg p-2.5">
      <header className="mb-1 flex items-center gap-1.5">
        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-accent/15 text-[11px] font-bold text-accent">
          {n}
        </span>
        <h4 className="text-[12px] font-semibold text-fg">{title}</h4>
      </header>
      <p className="mb-1.5 text-[11px] leading-relaxed text-fg-muted">{body}</p>
      <div className="relative">
        <pre className="overflow-x-auto rounded border border-border bg-bg-subtle px-2 py-1.5 font-mono text-[10.5px] leading-snug text-fg">
          {snippet}
        </pre>
        <button
          type="button"
          className="absolute right-1 top-1 rounded p-1 text-fg-subtle hover:bg-bg hover:text-fg"
          onClick={() => {
            void navigator.clipboard.writeText(snippet);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          }}
          title="copy"
        >
          {copied ? (
            <Check className="h-3 w-3 text-success" />
          ) : (
            <Copy className="h-3 w-3" />
          )}
        </button>
      </div>
    </div>
  );
}

function CallToActionRow({
  diagnose,
  onWildcardConfigured,
}: {
  diagnose: SubdomainDiagnose;
  onWildcardConfigured: () => void;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!diagnose.provider_configured) {
    return (
      <Link
        to="/settings"
        className="mt-1 inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-2 py-1 text-[11px] font-medium text-accent hover:bg-accent/20"
      >
        <SettingsIcon className="h-3 w-3" />
        {t("env_settings.subdomain.cta.open_settings")}
      </Link>
    );
  }
  if (diagnose.tunnel_mode === "local_config") {
    return (
      <Link
        to="/settings"
        className="mt-1 inline-flex items-center gap-1 rounded border border-warn/40 bg-warn/10 px-2 py-1 text-[11px] font-medium text-warn hover:bg-warn/20"
      >
        <ExternalLink className="h-3 w-3" />
        {t("env_settings.subdomain.cta.open_migration")}
      </Link>
    );
  }
  if (
    diagnose.tunnel_mode === "remote_managed" &&
    !diagnose.tunnel_has_wildcard
  ) {
    return (
      <div className="mt-1 space-y-1">
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              await ensureCloudflareWildcard();
              onWildcardConfigured();
            } catch (e) {
              setErr(
                e instanceof ApiError
                  ? e.reason
                  : e instanceof Error
                    ? e.message
                    : String(e),
              );
            } finally {
              setBusy(false);
            }
          }}
          className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-2 py-1 text-[11px] font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
        >
          <Zap className="h-3 w-3" />
          {busy
            ? t("env_settings.subdomain.cta.configuring")
            : t("env_settings.subdomain.cta.configure_wildcard")}
        </button>
        {err ? <p className="text-[11px] text-danger">{err}</p> : null}
      </div>
    );
  }
  if (
    diagnose.tunnel_mode === "remote_managed" &&
    diagnose.tunnel_has_wildcard &&
    !diagnose.e2e_reachable
  ) {
    return <OpenCertGuideButton />;
  }
  return null;
}

function OpenCertGuideButton() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-1 inline-flex items-center gap-1 rounded border border-warn/40 bg-warn/10 px-2 py-1 text-[11px] font-medium text-warn hover:bg-warn/20"
      >
        <CircleAlert className="h-3 w-3" />
        {t("env_settings.subdomain.cta.fix_cert")}
      </button>
      <WildcardCertGuide open={open} onClose={() => setOpen(false)} />
    </>
  );
}

function CheckLine({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail: string;
}) {
  return (
    <div className="flex items-baseline gap-1.5 text-[11.5px]">
      {ok ? (
        <Check className="h-3 w-3 shrink-0 text-success" strokeWidth={2.5} />
      ) : (
        <CircleAlert className="h-3 w-3 shrink-0 text-danger" strokeWidth={2} />
      )}
      <span className="font-medium text-fg">{label}</span>
      <span className="font-mono text-[10.5px] text-fg-subtle truncate">{detail}</span>
    </div>
  );
}

// Local Section container used by the SubdomainSetupGuide block —
// shaped to match the EnvironmentEditor's outer Section styling so
// the modal reads as one coherent surface.
function GuideSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-md border border-border bg-bg-subtle/30 p-3">
      <header className="mb-2">
        <h3 className="text-[12.5px] font-semibold text-fg">{title}</h3>
        {hint ? (
          <p className="mt-0.5 text-[11px] leading-relaxed text-fg-muted">
            {hint}
          </p>
        ) : null}
      </header>
      <div className="space-y-2">{children}</div>
    </section>
  );
}
