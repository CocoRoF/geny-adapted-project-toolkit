/** Comprehensive environment settings panel.
 *
 * Lives in the Deploy view (env card → ⚙ button). Surfaces every
 * `deploy_target_config` knob in one place so the operator doesn't
 * have to chase between the Environments page (JSON textarea), the
 * Stack section's Overrides drawer, and the Stack header's
 * path/subdomain toggle.
 *
 * Sections (each with its own explainer):
 *   * 라우팅 전략 — path | subdomain segmented control
 *   * Upstream — primary_service, primary_port, scheme, host
 *     header, tls verify, strip_prefix
 *   * 배포 동작 — build flag, require_2fa, cost_multiplier
 *
 * Save → `updateEnvironment` persists to `deploy_target_config` +
 * env fields. "Save & re-route" additionally calls `rerouteStack`
 * to immediately re-register Caddy routes with the new config (only
 * useful when a stack is currently running). */

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
  Sparkles,
  Zap,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type EnvironmentResponse,
  type EnvironmentPayload,
  type SubdomainDiagnose,
  diagnoseSubdomainMode,
  rerouteStack,
  updateEnvironment,
} from "@/api/environments";
import { ensureCloudflareWildcard } from "@/api/providers";
import { useI18n } from "@/app/providers/i18n-context";
import { StackRerouteHelpModal } from "@/ide/StackRerouteHelpModal";
import { WildcardCertGuide } from "@/ide/WildcardCertGuide";
import { Button } from "@/ui/Button";
import { Modal } from "@/ui/Modal";
import { cn } from "@/ui/cn";

/** Concrete scenario presets. One click sets every field to a known-
 * good combination so the operator doesn't have to translate their
 * stack architecture into knob values from scratch. Each preset is
 * a partial FormState merged onto the current form. */
interface ScenarioPreset {
  id: string;
  // i18n keys — name + one-line description rendered on the chip.
  name_key: string;
  hint_key: string;
  /** Recommended-for matching pattern — when the user's current
   * config already roughly matches, mark the preset as "current".
   * Returns true if `form` looks like this preset is already active. */
  matches: (form: FormState) => boolean;
  apply: (form: FormState) => FormState;
}

const PRESETS: ScenarioPreset[] = [
  {
    id: "nextjs-dev",
    name_key: "env_settings.preset.nextjs_dev",
    hint_key: "env_settings.preset.nextjs_dev_hint",
    matches: (f) =>
      f.preview_mode === "path" &&
      f.strip_prefix === true &&
      f.upstream_scheme !== "https" &&
      !f.upstream_tls_insecure,
    apply: (f) => ({
      ...f,
      preview_mode: "path",
      strip_prefix: true,
      upstream_scheme: "",
      upstream_tls_insecure: false,
      upstream_host_header: "",
      primary_service: f.primary_service || "frontend",
      primary_port: f.primary_port || "3000",
    }),
  },
  {
    id: "nextjs-prod-basepath",
    name_key: "env_settings.preset.nextjs_prod_basepath",
    hint_key: "env_settings.preset.nextjs_prod_basepath_hint",
    matches: (f) =>
      f.preview_mode === "path" &&
      f.strip_prefix === false &&
      f.upstream_scheme !== "https",
    apply: (f) => ({
      ...f,
      preview_mode: "path",
      strip_prefix: false,
      upstream_scheme: "",
      upstream_tls_insecure: false,
      upstream_host_header: "",
      primary_service: f.primary_service || "frontend",
      primary_port: f.primary_port || "3000",
    }),
  },
  {
    id: "tls-terminator",
    name_key: "env_settings.preset.tls_terminator",
    hint_key: "env_settings.preset.tls_terminator_hint",
    matches: (f) =>
      f.preview_mode === "path" &&
      f.upstream_scheme === "https" &&
      f.upstream_tls_insecure === true,
    apply: (f) => ({
      ...f,
      preview_mode: "path",
      strip_prefix: true,
      upstream_scheme: "https",
      upstream_tls_insecure: true,
      upstream_host_header: f.upstream_host_header || "",
      primary_service: f.primary_service || "nginx",
      primary_port: "443",
    }),
  },
  {
    id: "subdomain",
    name_key: "env_settings.preset.subdomain",
    hint_key: "env_settings.preset.subdomain_hint",
    matches: (f) => f.preview_mode === "subdomain",
    apply: (f) => ({
      ...f,
      preview_mode: "subdomain",
      strip_prefix: false, // host-keyed routing, prefix doesn't apply
      // Upstream fields stay as user-configured; subdomain doesn't
      // touch them.
    }),
  },
];

interface Props {
  open: boolean;
  env: EnvironmentResponse;
  onClose: () => void;
  onSaved: (updated: EnvironmentResponse) => void;
}

interface FormState {
  // routing
  preview_mode: "path" | "subdomain";
  preview_slug: string;
  // upstream
  primary_service: string;
  primary_port: string;
  upstream_scheme: "" | "http" | "https";
  upstream_host_header: string;
  upstream_tls_insecure: boolean;
  strip_prefix: boolean;
  // deploy
  build: boolean;
  require_2fa: boolean;
  cost_multiplier: string;
}

function readForm(env: EnvironmentResponse): FormState {
  const cfg = env.deploy_target_config ?? {};
  const mode = cfg.preview_mode === "subdomain" ? "subdomain" : "path";
  return {
    preview_mode: mode as "path" | "subdomain",
    preview_slug:
      typeof cfg.preview_slug === "string" ? cfg.preview_slug : "",
    primary_service:
      typeof cfg.primary_service === "string" ? cfg.primary_service : "",
    primary_port:
      typeof cfg.primary_port === "number" ? String(cfg.primary_port) : "",
    upstream_scheme:
      cfg.upstream_scheme === "https" || cfg.upstream_scheme === "http"
        ? cfg.upstream_scheme
        : "",
    upstream_host_header:
      typeof cfg.upstream_host_header === "string"
        ? cfg.upstream_host_header
        : "",
    upstream_tls_insecure: cfg.upstream_tls_insecure === true,
    strip_prefix:
      typeof cfg.strip_prefix === "boolean" ? cfg.strip_prefix : true,
    build: cfg.build === true,
    require_2fa: env.require_2fa ?? false,
    cost_multiplier: String(env.cost_multiplier ?? 1),
  };
}

function writeConfig(
  env: EnvironmentResponse,
  form: FormState,
): Record<string, unknown> {
  const cfg = { ...(env.deploy_target_config ?? {}) };
  cfg.preview_mode = form.preview_mode;
  const trimmedSlug = form.preview_slug.trim().toLowerCase();
  if (trimmedSlug) {
    cfg.preview_slug = trimmedSlug;
  } else {
    delete cfg.preview_slug;
  }
  if (form.primary_service.trim()) {
    cfg.primary_service = form.primary_service.trim();
  } else {
    delete cfg.primary_service;
  }
  const port = Number.parseInt(form.primary_port, 10);
  if (Number.isFinite(port) && port > 0) {
    cfg.primary_port = port;
  } else {
    delete cfg.primary_port;
  }
  if (form.upstream_scheme) {
    cfg.upstream_scheme = form.upstream_scheme;
  } else {
    delete cfg.upstream_scheme;
  }
  if (form.upstream_host_header.trim()) {
    cfg.upstream_host_header = form.upstream_host_header.trim();
  } else {
    delete cfg.upstream_host_header;
  }
  cfg.upstream_tls_insecure = form.upstream_tls_insecure;
  cfg.strip_prefix = form.strip_prefix;
  cfg.build = form.build;
  return cfg;
}

export function EnvSettingsModal({ open, env, onClose, onSaved }: Props) {
  const { t } = useI18n();
  const [form, setForm] = useState<FormState>(() => readForm(env));
  const [saving, setSaving] = useState<"save" | "save_reroute" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  const save = async (alsoReroute: boolean) => {
    setSaving(alsoReroute ? "save_reroute" : "save");
    setErr(null);
    setFlash(null);
    try {
      const payload: EnvironmentPayload = {
        name: env.name,
        deploy_target_kind: env.deploy_target_kind,
        deploy_target_config: writeConfig(env, form),
        require_2fa: form.require_2fa,
        cost_multiplier: Number(form.cost_multiplier) || 1,
      };
      const updated = await updateEnvironment(env.id, payload);
      onSaved(updated);
      if (alsoReroute) {
        const r = await rerouteStack(env.id, {
          preview_mode: form.preview_mode,
          primary_service: form.primary_service.trim() || null,
          primary_port: Number.parseInt(form.primary_port, 10) || null,
          strip_prefix: form.strip_prefix,
          upstream_scheme:
            (form.upstream_scheme as "http" | "https") || null,
          upstream_host_header: form.upstream_host_header.trim() || null,
          upstream_tls_insecure: form.upstream_tls_insecure,
        });
        if (r.ok) {
          setFlash(t("env_settings.saved_and_rerouted"));
        } else {
          setErr(
            t("env_settings.reroute_failed") +
              "\n" +
              r.output.slice(-300),
          );
        }
      } else {
        setFlash(t("env_settings.saved"));
      }
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.reason
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setSaving(null);
    }
  };

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
        {/* ── 시나리오 프리셋 (한 클릭 자동 채움) ── */}
        <Section
          title={t("env_settings.section.presets")}
          hint={t("env_settings.section.presets_hint")}
        >
          <div className="grid grid-cols-2 gap-2">
            {PRESETS.map((p) => {
              const active = p.matches(form);
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setForm(p.apply)}
                  className={cn(
                    "flex flex-col gap-1 rounded-md border px-3 py-2 text-left transition-colors",
                    active
                      ? "border-accent/60 bg-accent/10"
                      : "border-border bg-bg hover:bg-bg-subtle",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <Sparkles
                      className={cn(
                        "h-3 w-3 shrink-0",
                        active ? "text-accent" : "text-fg-subtle",
                      )}
                      strokeWidth={1.5}
                    />
                    <span
                      className={cn(
                        "text-[12px] font-semibold",
                        active ? "text-accent" : "text-fg",
                      )}
                    >
                      {t(p.name_key as never)}
                    </span>
                    {active ? (
                      <span className="ml-auto rounded-full bg-accent/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-accent">
                        {t("env_settings.preset.current")}
                      </span>
                    ) : null}
                  </div>
                  <p className="text-[10.5px] leading-snug text-fg-muted">
                    {t(p.hint_key as never)}
                  </p>
                </button>
              );
            })}
          </div>
        </Section>

        {/* ── 라우팅 전략 ── */}
        <Section
          title={t("env_settings.section.routing")}
          hint={t("env_settings.section.routing_hint")}
        >
          <div className="flex gap-2">
            <ModeButton
              active={form.preview_mode === "path"}
              label="path"
              hint={t("env_settings.mode.path.short")}
              onClick={() =>
                setForm((f) => ({ ...f, preview_mode: "path" }))
              }
            />
            <ModeButton
              active={form.preview_mode === "subdomain"}
              label="subdomain"
              hint={t("env_settings.mode.subdomain.short")}
              onClick={() =>
                setForm((f) => ({ ...f, preview_mode: "subdomain" }))
              }
            />
          </div>
          {form.preview_mode === "path" ? (
            <Field
              label={t("env_settings.strip_prefix")}
              hint={t("env_settings.strip_prefix_hint")}
            >
              <Toggle
                value={form.strip_prefix}
                onChange={(v) => setForm((f) => ({ ...f, strip_prefix: v }))}
              />
            </Field>
          ) : null}
          <Field
            label={t("env_settings.preview_slug")}
            hint={t("env_settings.preview_slug_hint").replace(
              "{default}",
              `prod-${env.name}-${env.project_id}`.toLowerCase(),
            )}
          >
            <Input
              value={form.preview_slug}
              placeholder={`prod-${env.name}-${env.project_id}`.toLowerCase()}
              onChange={(v) => setForm((f) => ({ ...f, preview_slug: v }))}
            />
          </Field>
        </Section>

        {form.preview_mode === "subdomain" ? <SubdomainSetupGuide /> : null}

        {/* ── Upstream ── */}
        <Section
          title={t("env_settings.section.upstream")}
          hint={t("env_settings.section.upstream_hint")}
        >
          <div className="grid grid-cols-2 gap-3">
            <Field
              label={t("env_settings.primary_service")}
              hint={t("env_settings.primary_service_hint")}
            >
              <Input
                value={form.primary_service}
                placeholder="nginx / frontend / (자동)"
                onChange={(v) =>
                  setForm((f) => ({ ...f, primary_service: v }))
                }
              />
            </Field>
            <Field
              label={t("env_settings.primary_port")}
              hint={t("env_settings.primary_port_hint")}
            >
              <Input
                value={form.primary_port}
                placeholder="3000 / 80 / 443"
                inputMode="numeric"
                onChange={(v) =>
                  setForm((f) => ({ ...f, primary_port: v }))
                }
              />
            </Field>
            <Field
              label={t("env_settings.upstream_scheme")}
              hint={t("env_settings.upstream_scheme_hint")}
            >
              <Select
                value={form.upstream_scheme}
                onChange={(v) =>
                  setForm((f) => ({
                    ...f,
                    upstream_scheme: v as "" | "http" | "https",
                  }))
                }
                options={[
                  { value: "", label: t("env_settings.inherit") },
                  { value: "http", label: "http" },
                  { value: "https", label: "https" },
                ]}
              />
            </Field>
            <Field
              label={t("env_settings.upstream_tls_insecure")}
              hint={t("env_settings.upstream_tls_insecure_hint")}
            >
              <Toggle
                value={form.upstream_tls_insecure}
                onChange={(v) =>
                  setForm((f) => ({ ...f, upstream_tls_insecure: v }))
                }
              />
            </Field>
            <Field
              label={t("env_settings.upstream_host_header")}
              hint={t("env_settings.upstream_host_header_hint")}
              span={2}
            >
              <Input
                value={form.upstream_host_header}
                placeholder="example.com (비우면 passthrough)"
                onChange={(v) =>
                  setForm((f) => ({ ...f, upstream_host_header: v }))
                }
              />
            </Field>
          </div>
        </Section>

        {/* ── 배포 동작 ── */}
        <Section
          title={t("env_settings.section.deploy")}
          hint={t("env_settings.section.deploy_hint")}
        >
          <div className="grid grid-cols-2 gap-3">
            <Field
              label={t("env_settings.build")}
              hint={t("env_settings.build_hint")}
            >
              <Toggle
                value={form.build}
                onChange={(v) => setForm((f) => ({ ...f, build: v }))}
              />
            </Field>
            <Field
              label={t("env_settings.require_2fa")}
              hint={t("env_settings.require_2fa_hint")}
            >
              <Toggle
                value={form.require_2fa}
                onChange={(v) =>
                  setForm((f) => ({ ...f, require_2fa: v }))
                }
              />
            </Field>
            <Field
              label={t("env_settings.cost_multiplier")}
              hint={t("env_settings.cost_multiplier_hint")}
            >
              <Input
                value={form.cost_multiplier}
                inputMode="decimal"
                onChange={(v) =>
                  setForm((f) => ({ ...f, cost_multiplier: v }))
                }
              />
            </Field>
          </div>
        </Section>

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

/** Step-by-step subdomain-mode prerequisites + live diagnose.
 *
 * Surfaces the THREE things the operator has to get right for
 * subdomain mode to work end-to-end:
 *   1. Wildcard DNS pointing the `*.<preview-domain>` at the GAPT
 *      edge (Caddy / Cloudflare Tunnel).
 *   2. Cloudflare Tunnel ingress including the wildcard (if using
 *      cloudflared).
 *   3. GAPT_CADDY_PREVIEW_DOMAIN env var set on the server.
 *
 * Each step has a copy-able snippet. The diagnose button runs the
 * server-side check and reports which step still needs work — so
 * the operator doesn't have to debug "the URL doesn't load" by
 * hand. */
function SubdomainSetupGuide() {
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

  // Run diagnose once on mount so we can decide whether the manual
  // setup snippets are still relevant — if the Cloudflare provider
  // is fully wired up, the operator should never see CNAME / yaml
  // snippets, just confirmation that everything's handled.
  useEffect(() => {
    void diagnose();
  }, [diagnose]);

  // "Provider handles DNS + tunnel ingress for us" — collapses the
  // manual snippets into a compact success card. We deliberately
  // don't require e2e_reachable here: the wildcard TLS cert is a
  // separate Cloudflare-side prerequisite the provider can't issue
  // (Universal SSL doesn't cover wildcards). The diagnose block
  // below still surfaces that when it's missing.
  const providerHandled =
    !!result &&
    result.provider_configured &&
    result.tunnel_mode === "remote_managed" &&
    result.tunnel_has_wildcard;

  // Best guess for the domain to put in the snippets. If diagnose
  // ran, use what the server reported; otherwise fall back to a
  // placeholder. The user will replace the placeholder anyway —
  // this just makes the snippet less abstract.
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
    <Section
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
            onClick={diagnose}
            disabled={running}
            className="ml-auto"
          >
            {running ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : null}
            {t("env_settings.subdomain.diagnose.run")}
          </Button>
        </header>
        {err ? (
          <p className="text-[11px] text-danger">{err}</p>
        ) : null}
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
    </Section>
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

/** Inline call-to-action surfaced after the CheckLines. Chooses the
 * single highest-leverage action based on the diagnose state:
 *
 *   provider missing       → "Open Settings → Providers"
 *   local_config mode      → "Open Settings → Migration wizard"
 *   remote_managed + !wc   → "Configure wildcard ingress" (inline)
 *   all green              → nothing (banner above handles it)
 *
 * The inline configure-wildcard call hits the same backend endpoint
 * as the Settings page button — keeps the operator in the env modal
 * when one click is all that's needed. */
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
  // Everything provider-side is green but the e2e probe still fails
  // — that's almost always the missing wildcard cert from Cloudflare.
  // Open the dedicated guide modal instead of dumping the whole
  // "issue an Advanced Certificate or enable Total TLS" wall into
  // the next_steps list.
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

// ─────────────────────────────────────────── small atoms ──

function Section({
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

function Field({
  label,
  hint,
  span,
  children,
}: {
  label: string;
  hint?: string;
  span?: number;
  children: React.ReactNode;
}) {
  return (
    <label
      className={cn("flex flex-col gap-1", span === 2 && "col-span-2")}
    >
      <span className="text-[10.5px] uppercase tracking-wider text-fg-subtle">
        {label}
      </span>
      {children}
      {hint ? (
        <span className="text-[10.5px] text-fg-subtle">{hint}</span>
      ) : null}
    </label>
  );
}

function Input({
  value,
  placeholder,
  inputMode,
  onChange,
}: {
  value: string;
  placeholder?: string;
  inputMode?: "numeric" | "decimal" | "text";
  onChange: (v: string) => void;
}) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      inputMode={inputMode}
      className="rounded border border-border bg-bg px-2 py-1 font-mono text-[12px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
      spellCheck={false}
    />
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded border border-border bg-bg px-2 py-1 font-mono text-[12px] text-fg"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function Toggle({
  value,
  onChange,
}: {
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      onClick={() => onChange(!value)}
      className={cn(
        "inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors",
        value
          ? "border-accent/40 bg-accent/30"
          : "border-border bg-bg-subtle",
      )}
    >
      <span
        className={cn(
          "h-3.5 w-3.5 rounded-full bg-fg-muted shadow transition-transform",
          value ? "translate-x-5" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

function ModeButton({
  active,
  label,
  hint,
  onClick,
}: {
  active: boolean;
  label: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex-1 rounded-md border px-3 py-2 text-left transition-colors",
        active
          ? "border-accent/60 bg-accent/10"
          : "border-border bg-bg hover:bg-bg-subtle",
      )}
    >
      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            "inline-block h-2 w-2 rounded-full",
            active ? "bg-accent" : "bg-fg-subtle/40",
          )}
        />
        <span
          className={cn(
            "font-mono text-[12px] font-semibold",
            active ? "text-accent" : "text-fg",
          )}
        >
          {label}
        </span>
      </div>
      <p className="mt-1 text-[10.5px] leading-snug text-fg-muted">{hint}</p>
    </button>
  );
}
