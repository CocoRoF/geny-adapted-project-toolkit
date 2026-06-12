/**
 * Phase H — unified environment editor.
 *
 * One component used by:
 *   - `NewEnvironmentModal` (Project → Environments + → "+ New environment")
 *     with `mode="create"`.
 *   - `EnvSettingsModal` (Deploy view → env card → ⚙ Edit) with
 *     `mode="edit"`.
 *
 * Renders the form fields only — the wrapping modal owns the modal
 * chrome (title, footer buttons, dismiss behaviour). Form state is
 * lifted to the parent so the parent's Save button can submit and
 * pass the same state to follow-up actions like "Save & re-route".
 *
 * Field-level errors come from the backend's `environment.target_config_invalid`
 * 422 response (`detail.fields = [{loc, msg, type}]`). The parent
 * catches the 422 and passes `fieldErrors` back here so each field
 * shows its message inline rather than a single banner.
 *
 * The 4 EnvSettingsModal presets + the legacy NewEnvironmentModal
 * TLS-terminator toggle are merged into one preset row that lives
 * in `LocalSection` (preset only makes sense for kind=local). See
 * [`docs/plan/m2_phase_h.md`](../../../docs/plan/m2_phase_h.md) §H.2.
 */

import { Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { DeployTargetKind } from "@/api/environments";
import { type FieldError, type FormState, readForm, writeForm } from "@/environments/env-form";
import { useI18n } from "@/app/providers/i18n-context";
import { cn } from "@/ui/cn";

// ──────────────────────────────────────── component ──

interface Props {
  mode: "create" | "edit";
  /** Project the env belongs to — used for secret pickers. */
  projectId: string;
  form: FormState;
  onFormChange: (next: FormState) => void;
  /** Backend-provided per-field errors from the 422 response. */
  fieldErrors?: FieldError[];
  /** Read-only mode (e.g. while saving). */
  disabled?: boolean;
  /** Allow consumers (EnvSettingsModal) to render extra panels (the
   * subdomain setup guide) directly below the kind-specific section. */
  extraBelowKindSection?: React.ReactNode;
}

export function EnvironmentEditor({
  mode,
  projectId,
  form,
  onFormChange,
  fieldErrors,
  disabled = false,
  extraBelowKindSection,
}: Props) {
  const { t } = useI18n();

  /** Field-error lookup. Pydantic loc is the dotted path inside the
   * payload — we anchor errors to form fields by the *config* key
   * they map to. e.g. `loc=["primary_port"]` → "primary_port" form
   * field. */
  const errorByConfigKey = useMemo(() => {
    const out = new Map<string, string>();
    for (const e of fieldErrors ?? []) {
      const key = e.loc.map(String).join(".");
      if (!out.has(key)) out.set(key, e.msg);
    }
    return out;
  }, [fieldErrors]);

  function setKind(next: DeployTargetKind) {
    if (mode === "edit") return; // disabled in edit
    // Wipe kind-specific fields with the new kind's defaults so the
    // operator doesn't see ghost values from a previous kind.
    const blank = readForm(undefined, next);
    onFormChange({
      ...blank,
      // Common state stays:
      name: form.name,
      require_2fa: form.require_2fa,
      cost_multiplier: form.cost_multiplier,
    });
  }

  return (
    <div className="space-y-3.5">
      {/* ── Basic — every kind ── */}
      <div className="grid grid-cols-2 gap-3">
        <Field label={t("env_editor.name")} hint={t("env_editor.name_hint")}>
          <Input
            value={form.name}
            onChange={(v) => onFormChange({ ...form, name: v })}
            placeholder="staging"
            disabled={disabled}
          />
        </Field>
        <Field label={t("env_editor.kind")}>
          <select
            value={form.kind}
            onChange={(e) => setKind(e.target.value as DeployTargetKind)}
            disabled={disabled || mode === "edit"}
            className="flex h-8 w-full appearance-none rounded-md border border-border bg-surface px-2.5 pr-7 py-1.5 text-[13px] text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            <option value="local">{t("env_editor.kind.local")}</option>
            <option value="remote_ssh">{t("env_editor.kind.remote_ssh")}</option>
            <option value="webhook">{t("env_editor.kind.webhook")}</option>
            <option value="k8s">{t("env_editor.kind.k8s")}</option>
          </select>
        </Field>
      </div>

      {/* ── Kind-specific body ── */}
      {form.kind === "local" ? (
        <LocalSection
          form={form}
          onFormChange={onFormChange}
          errorByConfigKey={errorByConfigKey}
          disabled={disabled}
        />
      ) : null}
      {form.kind === "remote_ssh" ? (
        <RemoteSshSection
          form={form}
          onFormChange={onFormChange}
          errorByConfigKey={errorByConfigKey}
          disabled={disabled}
          projectId={projectId}
        />
      ) : null}
      {form.kind === "webhook" ? (
        <WebhookSection
          form={form}
          onFormChange={onFormChange}
          errorByConfigKey={errorByConfigKey}
          disabled={disabled}
          projectId={projectId}
        />
      ) : null}
      {form.kind === "k8s" ? <K8sNotSupportedBanner /> : null}

      {/* Phase M.5 — power-user escape hatch. The structured fields
          above cover every well-known key, but operators with a custom
          deploy script (or with the saved row from a different GAPT
          version) sometimes need to verify what's actually going to
          POST. The preview is read-only — to mutate, use the structured
          fields above. Writing raw JSON would risk silent schema drift
          + bypass the field-level error inspector. */}
      <RawConfigPreview form={form} />

      {extraBelowKindSection}

      {/* ── Common policy fields ── */}
      <div className="grid grid-cols-2 gap-3">
        <Field label={t("env_editor.require_2fa")} hint={t("env_editor.require_2fa_hint")}>
          <label className="flex h-8 items-center gap-2 rounded-md border border-border bg-surface px-2.5 text-[13px]">
            <input
              type="checkbox"
              checked={form.require_2fa}
              onChange={(e) => onFormChange({ ...form, require_2fa: e.target.checked })}
              disabled={disabled}
            />
            {form.require_2fa
              ? t("env_editor.require_2fa.required")
              : t("env_editor.require_2fa.not_required")}
          </label>
        </Field>
        <Field label={t("env_editor.cost_multiplier")} hint={t("env_editor.cost_multiplier_hint")}>
          <Input
            value={form.cost_multiplier}
            onChange={(v) => onFormChange({ ...form, cost_multiplier: v })}
            inputMode="decimal"
            disabled={disabled}
          />
        </Field>
      </div>
    </div>
  );
}

// ────────────────────────────────────── local section ──

/**
 * Scenario presets — one click sets the routing/upstream knobs to a
 * known-good combination. Merges the 4 EnvSettingsModal presets with
 * the legacy NewEnvironmentModal TLS-terminator toggle, per Phase H
 * scope decision (sole entry point for TLS-terminator now).
 */
const _PRESETS: {
  id: string;
  name_key: string;
  hint_key: string;
  apply: (f: FormState) => FormState;
  matches: (f: FormState) => boolean;
}[] = [
  {
    id: "nextjs-dev",
    name_key: "env_editor.preset.nextjs_dev",
    hint_key: "env_editor.preset.nextjs_dev_hint",
    matches: (f) =>
      f.preview_mode === "path" &&
      f.strip_prefix === "true" &&
      f.upstream_scheme !== "https" &&
      !f.upstream_tls_insecure,
    apply: (f) => ({
      ...f,
      preview_mode: "path",
      strip_prefix: "true",
      upstream_scheme: "",
      upstream_tls_insecure: false,
      upstream_host_header: "",
      primary_service: f.primary_service || "frontend",
      primary_port: f.primary_port || "3000",
    }),
  },
  {
    id: "nextjs-prod-basepath",
    name_key: "env_editor.preset.nextjs_prod_basepath",
    hint_key: "env_editor.preset.nextjs_prod_basepath_hint",
    matches: (f) =>
      f.preview_mode === "path" && f.strip_prefix === "false" && f.upstream_scheme !== "https",
    apply: (f) => ({
      ...f,
      preview_mode: "path",
      strip_prefix: "false",
      upstream_scheme: "",
      upstream_tls_insecure: false,
      upstream_host_header: "",
      primary_service: f.primary_service || "frontend",
      primary_port: f.primary_port || "3000",
    }),
  },
  {
    id: "tls-terminator",
    name_key: "env_editor.preset.tls_terminator",
    hint_key: "env_editor.preset.tls_terminator_hint",
    matches: (f) =>
      f.preview_mode === "path" &&
      f.upstream_scheme === "https" &&
      f.upstream_tls_insecure === true,
    apply: (f) => ({
      ...f,
      preview_mode: "path",
      strip_prefix: "true",
      upstream_scheme: "https",
      upstream_tls_insecure: true,
      upstream_host_header: f.upstream_host_header,
      primary_service: f.primary_service || "nginx",
      primary_port: "443",
    }),
  },
  {
    id: "subdomain",
    name_key: "env_editor.preset.subdomain",
    hint_key: "env_editor.preset.subdomain_hint",
    matches: (f) => f.preview_mode === "subdomain",
    apply: (f) => ({
      ...f,
      preview_mode: "subdomain",
      strip_prefix: "false",
    }),
  },
];

interface SectionProps {
  form: FormState;
  onFormChange: (next: FormState) => void;
  errorByConfigKey: Map<string, string>;
  disabled?: boolean;
}

function LocalSection({ form, onFormChange, errorByConfigKey, disabled }: SectionProps) {
  const { t } = useI18n();
  return (
    <Section title={t("env_editor.section.compose")}>
      <Field label={t("env_editor.compose_path")} hint={t("env_editor.compose_path_hint")}>
        <Input
          value={form.compose_path}
          onChange={(v) => onFormChange({ ...form, compose_path: v })}
          placeholder="docker-compose.yml"
          disabled={disabled}
          error={errorByConfigKey.get("compose_path")}
        />
      </Field>
      <Field label={t("env_editor.compose_paths")} hint={t("env_editor.compose_paths_hint")}>
        <Input
          value={form.compose_paths_csv}
          onChange={(v) => onFormChange({ ...form, compose_paths_csv: v })}
          placeholder="a.yml, b.yml"
          disabled={disabled}
        />
      </Field>

      <Section title={t("env_editor.section.presets")} sub>
        <div className="grid grid-cols-2 gap-2">
          {_PRESETS.map((p) => {
            const active = p.matches(form);
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => onFormChange(p.apply(form))}
                disabled={disabled}
                className={cn(
                  "flex flex-col gap-1 rounded-md border px-3 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                  active
                    ? "border-accent/60 bg-accent/10"
                    : "border-border bg-bg hover:bg-bg-subtle",
                )}
              >
                <div className="flex items-center gap-1.5">
                  <Sparkles
                    className={cn("h-3 w-3 shrink-0", active ? "text-accent" : "text-fg-subtle")}
                    strokeWidth={1.5}
                  />
                  <span
                    className={cn("text-[12px] font-semibold", active ? "text-accent" : "text-fg")}
                  >
                    {t(p.name_key as never)}
                  </span>
                  {active ? (
                    <span className="ml-auto rounded-full bg-accent/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-accent">
                      {t("env_editor.preset.current")}
                    </span>
                  ) : null}
                </div>
                <p className="text-[10.5px] leading-snug text-fg-muted">{t(p.hint_key as never)}</p>
              </button>
            );
          })}
        </div>
      </Section>

      <Section title={t("env_editor.section.routing")} sub>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("env_editor.preview_mode")}>
            <select
              value={form.preview_mode}
              onChange={(e) =>
                onFormChange({
                  ...form,
                  preview_mode: e.target.value as "" | "path" | "subdomain",
                })
              }
              disabled={disabled}
              className="flex h-8 w-full appearance-none rounded-md border border-border bg-surface px-2.5 pr-7 py-1.5 text-[13px] text-fg disabled:opacity-50"
            >
              <option value="">{t("env_editor.inherit")}</option>
              <option value="path">path</option>
              <option value="subdomain">subdomain</option>
            </select>
          </Field>
          <Field label={t("env_editor.preview_slug")} hint={t("env_editor.preview_slug_hint")}>
            <Input
              value={form.preview_slug}
              onChange={(v) => onFormChange({ ...form, preview_slug: v })}
              disabled={disabled}
              error={errorByConfigKey.get("preview_slug")}
            />
          </Field>
          <Field label={t("env_editor.strip_prefix")}>
            <select
              value={form.strip_prefix}
              onChange={(e) =>
                onFormChange({
                  ...form,
                  strip_prefix: e.target.value as "" | "true" | "false",
                })
              }
              disabled={disabled}
              className="flex h-8 w-full appearance-none rounded-md border border-border bg-surface px-2.5 pr-7 py-1.5 text-[13px] text-fg disabled:opacity-50"
            >
              <option value="">{t("env_editor.inherit")}</option>
              <option value="true">{t("env_editor.strip_prefix.on")}</option>
              <option value="false">{t("env_editor.strip_prefix.off")}</option>
            </select>
          </Field>
        </div>
      </Section>

      <Section title={t("env_editor.section.upstream")} sub>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("env_editor.primary_service")}>
            <Input
              value={form.primary_service}
              onChange={(v) => onFormChange({ ...form, primary_service: v })}
              placeholder="nginx / frontend"
              disabled={disabled}
              error={errorByConfigKey.get("primary_service")}
            />
          </Field>
          <Field label={t("env_editor.primary_port")}>
            <Input
              value={form.primary_port}
              onChange={(v) => onFormChange({ ...form, primary_port: v })}
              inputMode="numeric"
              placeholder="3000"
              disabled={disabled}
              error={errorByConfigKey.get("primary_port")}
            />
          </Field>
          <Field label={t("env_editor.upstream_scheme")}>
            <select
              value={form.upstream_scheme}
              onChange={(e) =>
                onFormChange({
                  ...form,
                  upstream_scheme: e.target.value as "" | "http" | "https",
                })
              }
              disabled={disabled}
              className="flex h-8 w-full appearance-none rounded-md border border-border bg-surface px-2.5 pr-7 py-1.5 text-[13px] text-fg disabled:opacity-50"
            >
              <option value="">{t("env_editor.inherit")}</option>
              <option value="http">http</option>
              <option value="https">https</option>
            </select>
          </Field>
          <Field label={t("env_editor.upstream_tls_insecure")}>
            <label className="flex h-8 items-center gap-2 rounded-md border border-border bg-surface px-2.5 text-[13px]">
              <input
                type="checkbox"
                checked={form.upstream_tls_insecure}
                onChange={(e) => onFormChange({ ...form, upstream_tls_insecure: e.target.checked })}
                disabled={disabled}
              />
              {form.upstream_tls_insecure
                ? t("env_editor.upstream_tls_insecure.on")
                : t("env_editor.upstream_tls_insecure.off")}
            </label>
          </Field>
          <Field
            label={t("env_editor.upstream_host_header")}
            hint={t("env_editor.upstream_host_header_hint")}
            span={2}
          >
            <Input
              value={form.upstream_host_header}
              onChange={(v) => onFormChange({ ...form, upstream_host_header: v })}
              placeholder="example.com"
              disabled={disabled}
            />
          </Field>
          <Field label={t("env_editor.build")} hint={t("env_editor.build_hint")} span={2}>
            <label className="flex h-8 items-center gap-2 rounded-md border border-border bg-surface px-2.5 text-[13px]">
              <input
                type="checkbox"
                checked={form.build}
                onChange={(e) => onFormChange({ ...form, build: e.target.checked })}
                disabled={disabled}
              />
              {form.build ? t("env_editor.build.on") : t("env_editor.build.off")}
            </label>
          </Field>
        </div>
      </Section>

      {Object.keys(form.extras).length > 0 ? (
        <Section title={t("env_editor.extras")} sub>
          <p className="text-[10.5px] text-fg-subtle">{t("env_editor.extras_hint")}</p>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(form.extras).map(([k, v]) => (
              <span
                key={k}
                className="inline-flex items-center gap-1 rounded border border-border bg-bg px-1.5 py-0.5 font-mono text-[10.5px] text-fg-muted"
              >
                <span>
                  {k}={JSON.stringify(v)}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    const next = { ...form.extras };
                    delete next[k];
                    onFormChange({ ...form, extras: next });
                  }}
                  className="hover:text-danger"
                  title={t("env_editor.extras_remove")}
                  disabled={disabled}
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </span>
            ))}
          </div>
        </Section>
      ) : null}
    </Section>
  );
}

// ────────────────────────────────── remote_ssh section ──

function RemoteSshSection({
  form,
  onFormChange,
  errorByConfigKey,
  disabled,
  projectId,
}: SectionProps & { projectId: string }) {
  const { t } = useI18n();
  return (
    <Section
      title={t("env_editor.section.remote_ssh")}
      hint={t("env_editor.section.remote_ssh_hint")}
    >
      <div className="grid grid-cols-3 gap-3">
        <Field label={t("env_editor.host")} span={2} error={errorByConfigKey.get("host")}>
          <Input
            value={form.host}
            onChange={(v) => onFormChange({ ...form, host: v })}
            placeholder="prod-1.example.com"
            disabled={disabled}
            error={errorByConfigKey.get("host")}
          />
        </Field>
        <Field label={t("env_editor.port")} error={errorByConfigKey.get("port")}>
          <Input
            value={form.port}
            onChange={(v) => onFormChange({ ...form, port: v })}
            inputMode="numeric"
            placeholder="22"
            disabled={disabled}
            error={errorByConfigKey.get("port")}
          />
        </Field>
        <Field label={t("env_editor.user")}>
          <Input
            value={form.user}
            onChange={(v) => onFormChange({ ...form, user: v })}
            placeholder="deploy"
            disabled={disabled}
          />
        </Field>
        <Field
          label={t("env_editor.key_secret_ref")}
          hint={t("env_editor.key_secret_ref_hint")}
          span={2}
        >
          <SecretPicker
            scope="project"
            ownerId={projectId}
            value={form.key_secret_ref}
            onChange={(v) => onFormChange({ ...form, key_secret_ref: v })}
            disabled={disabled}
          />
        </Field>
        <Field
          label={t("env_editor.compose_path")}
          hint={t("env_editor.compose_path_hint")}
          span={3}
        >
          <Input
            value={form.remote_compose_path}
            onChange={(v) => onFormChange({ ...form, remote_compose_path: v })}
            placeholder="/srv/app/docker-compose.yml"
            disabled={disabled}
          />
        </Field>
      </div>
      <p className="text-[10.5px] text-warn">{t("env_editor.remote_ssh_form_only_notice")}</p>
    </Section>
  );
}

// ─────────────────────────────────── webhook section ──

function WebhookSection({
  form,
  onFormChange,
  errorByConfigKey,
  disabled,
  projectId,
}: SectionProps & { projectId: string }) {
  const { t } = useI18n();
  return (
    <Section title={t("env_editor.section.webhook")} hint={t("env_editor.section.webhook_hint")}>
      <Field
        label={t("env_editor.webhook_url")}
        hint={t("env_editor.webhook_url_hint")}
        error={errorByConfigKey.get("url")}
      >
        <Input
          value={form.webhook_url}
          onChange={(v) => onFormChange({ ...form, webhook_url: v })}
          placeholder="https://hook.example.com/deploy"
          disabled={disabled}
          error={errorByConfigKey.get("url")}
        />
      </Field>
      <Field
        label={t("env_editor.webhook_secret_ref")}
        hint={t("env_editor.webhook_secret_ref_hint")}
      >
        <SecretPicker
          scope="project"
          ownerId={projectId}
          value={form.webhook_secret_ref}
          onChange={(v) => onFormChange({ ...form, webhook_secret_ref: v })}
          disabled={disabled}
        />
      </Field>
      <Field label={t("env_editor.env_keys")} hint={t("env_editor.env_keys_hint")}>
        <Input
          value={form.env_keys_csv}
          onChange={(v) => onFormChange({ ...form, env_keys_csv: v })}
          placeholder="API_URL, DATABASE_URL"
          disabled={disabled}
        />
      </Field>
      <p className="text-[10.5px] text-warn">{t("env_editor.webhook_form_only_notice")}</p>
    </Section>
  );
}

function K8sNotSupportedBanner() {
  const { t } = useI18n();
  return (
    <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2.5">
      <p className="text-[12px] font-medium text-warn">{t("env_editor.k8s_unsupported.title")}</p>
      <p className="mt-1 text-[11px] leading-relaxed text-fg-muted">
        {t("env_editor.k8s_unsupported.body")}
      </p>
    </div>
  );
}

/** Phase M.5 — collapsible read-only JSON preview of what the form is
 * about to POST as `deploy_target_config`. Helps operators verify
 * before saving — especially valuable for `extras` (carried untouched)
 * and for cross-checking a structured edit against a known-good
 * config from a different project. Read-only by design; the
 * structured fields above remain the only mutation surface. */
function RawConfigPreview({ form }: { form: FormState }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  // Re-derive on each render — cheap (one JSON.stringify of the
  // <50-key dict). Avoids any "preview is stale relative to the form"
  // confusion.
  const config = useMemo(() => {
    try {
      return writeForm(form).deploy_target_config;
    } catch {
      return {};
    }
  }, [form]);
  return (
    <div className="rounded-md border border-border bg-bg-subtle">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-[11.5px] text-fg-muted hover:bg-bg"
      >
        <span>{t("env_editor.raw_preview.toggle")}</span>
        <span className="font-mono text-[10.5px] text-fg-subtle">{open ? "▼" : "▶"}</span>
      </button>
      {open ? (
        <pre className="max-h-64 overflow-auto border-t border-border bg-bg p-3 text-[11px] leading-snug text-fg-muted">
          {JSON.stringify(config, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}

// ──────────────────────────────── small UI primitives ──

function Section({
  title,
  hint,
  children,
  sub,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  sub?: boolean;
}) {
  return (
    <section
      className={cn(
        "rounded-md p-3",
        sub ? "border-0 bg-transparent p-0" : "border border-border bg-bg-subtle/30",
      )}
    >
      <header className={cn("mb-2", sub ? "mb-1.5" : null)}>
        <h3 className={cn("font-semibold text-fg", sub ? "text-[11.5px]" : "text-[12.5px]")}>
          {title}
        </h3>
        {hint ? <p className="mt-0.5 text-[11px] leading-relaxed text-fg-muted">{hint}</p> : null}
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
  error,
}: {
  label: string;
  hint?: string | undefined;
  span?: number | undefined;
  children: React.ReactNode;
  error?: string | undefined;
}) {
  return (
    <label
      className={cn("flex flex-col gap-1", span === 2 && "col-span-2", span === 3 && "col-span-3")}
    >
      <span className="text-[10.5px] uppercase tracking-wider text-fg-subtle">{label}</span>
      {children}
      {error ? (
        <span className="text-[10.5px] text-danger">{error}</span>
      ) : hint ? (
        <span className="text-[10.5px] text-fg-subtle">{hint}</span>
      ) : null}
    </label>
  );
}

function Input({
  value,
  onChange,
  placeholder,
  inputMode,
  disabled,
  error,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string | undefined;
  inputMode?: "numeric" | "decimal" | "text" | undefined;
  disabled?: boolean | undefined;
  error?: string | undefined;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      inputMode={inputMode}
      disabled={disabled}
      className={cn(
        "flex h-8 w-full rounded-md border bg-surface px-2.5 py-1.5 text-[13px] text-fg placeholder:text-fg-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50",
        error ? "border-danger/60" : "border-border",
      )}
      spellCheck={false}
    />
  );
}

// ──────────────────────────────── secret picker ──

function SecretPicker({
  scope,
  ownerId,
  value,
  onChange,
  disabled,
}: {
  scope: "project" | "system" | "environment";
  ownerId: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean | undefined;
}) {
  const { t } = useI18n();
  const [secrets, setSecrets] = useState<{ id: string; key_name: string }[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { listSecrets } = await import("@/api/secrets");
        const rows = await listSecrets({ scope, owner_id: ownerId });
        if (!cancelled) {
          setSecrets(rows.map((r) => ({ id: r.id, key_name: r.key_name })));
        }
      } catch {
        if (!cancelled) setSecrets([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [scope, ownerId]);
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled || secrets === null}
      className="flex h-8 w-full appearance-none rounded-md border border-border bg-surface px-2.5 pr-7 py-1.5 text-[13px] text-fg disabled:cursor-not-allowed disabled:opacity-50"
    >
      <option value="">{t("env_editor.secret_none")}</option>
      {(secrets ?? []).map((s) => (
        <option key={s.id} value={s.id}>
          {s.key_name}
        </option>
      ))}
    </select>
  );
}
