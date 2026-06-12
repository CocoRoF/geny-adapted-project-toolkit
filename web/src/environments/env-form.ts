/**
 * Phase H — environment editor FORM LOGIC (pure, component-free).
 *
 * Split out of `EnvironmentEditor.tsx` so the component file only
 * exports components (react-refresh requirement) and so the
 * denormalise/serialise pair is unit-testable without rendering.
 * `EnvironmentEditor` renders these shapes; `Environments` /
 * `EnvSettingsModal` drive create/edit flows through them.
 */

import type { DeployTargetKind, EnvironmentResponse } from "@/api/environments";

// ──────────────────────────────────────── shared form shape ──

/**
 * The denormalised editor form. Strings (not numbers) for inputs so
 * empty-state and partial-typing don't fight `<input type="number">`'s
 * built-in NaN handling — we convert at submit time in `writeForm`.
 *
 * Unknown legacy keys live in `extras` and round-trip untouched so a
 * row with `{"primary_port": 3000, "legacy_foo": "bar"}` doesn't lose
 * `legacy_foo` on save. The UI surfaces them in a read-only chip row
 * inside `LocalSection` so the operator can choose to clean them up.
 */
export interface FormState {
  // common (every kind)
  name: string;
  kind: DeployTargetKind;
  require_2fa: boolean;
  cost_multiplier: string;

  // local
  compose_path: string;
  compose_paths_csv: string; // user-edited as comma-separated; split on save
  preview_mode: "" | "path" | "subdomain";
  preview_slug: string;
  strip_prefix: "" | "true" | "false"; // "" = inherit deploy default
  primary_service: string;
  primary_port: string;
  upstream_scheme: "" | "http" | "https";
  upstream_host_header: string;
  upstream_tls_insecure: boolean;
  build: boolean;

  // remote_ssh
  host: string;
  user: string;
  port: string;
  key_secret_ref: string;
  remote_compose_path: string;

  // webhook
  webhook_url: string;
  webhook_secret_ref: string;
  env_keys_csv: string;

  // unknown legacy keys preserved verbatim
  extras: Record<string, unknown>;
}

export interface FieldError {
  loc: (string | number)[];
  msg: string;
  type?: string;
}

// ──────────────────────────────────────── kind defaults ──

/**
 * Returns a partial FormState carrying the *sensible defaults* for a
 * freshly-picked kind in create mode. The caller merges these onto
 * a blank form when the kind toggles.
 *
 * The defaults are deliberately small — they only seed values the
 * operator would otherwise have to type for a working baseline. We
 * don't pre-fill optional knobs (preview_slug, primary_service) so
 * the modal stays visually "empty" until the user explicitly tunes
 * the env.
 */
export function defaultsFor(kind: DeployTargetKind): Partial<FormState> {
  switch (kind) {
    case "local":
      return {
        compose_path: "docker-compose.yml",
        preview_mode: "path",
        strip_prefix: "true",
      };
    case "remote_ssh":
      return {
        port: "22",
        user: "deploy",
        remote_compose_path: "docker-compose.yml",
      };
    case "webhook":
      return {};
    case "k8s":
      return {};
  }
}

// ──────────────────────────────────────── read/write ──

const _KNOWN_LOCAL_KEYS = new Set([
  "compose_path",
  "compose_paths",
  "preview_mode",
  "preview_slug",
  "strip_prefix",
  "primary_service",
  "primary_port",
  "upstream_scheme",
  "upstream_host_header",
  "upstream_tls_insecure",
  "build",
]);

const _KNOWN_REMOTE_SSH_KEYS = new Set(["host", "user", "port", "key_secret_ref", "compose_path"]);

const _KNOWN_WEBHOOK_KEYS = new Set(["url", "secret_ref", "env_keys"]);

function knownKeysFor(kind: DeployTargetKind): Set<string> {
  if (kind === "local") return _KNOWN_LOCAL_KEYS;
  if (kind === "remote_ssh") return _KNOWN_REMOTE_SSH_KEYS;
  if (kind === "webhook") return _KNOWN_WEBHOOK_KEYS;
  return new Set();
}

/** Build a FormState from an existing environment (edit mode), or
 * from the kind's defaults (create mode when `initial` is undefined). */
export function readForm(
  initial: EnvironmentResponse | undefined,
  fallbackKind: DeployTargetKind = "local",
): FormState {
  const kind = initial?.deploy_target_kind ?? fallbackKind;
  const cfg: Record<string, unknown> = initial?.deploy_target_config ?? {};
  const knownKeys = knownKeysFor(kind);
  const extras: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(cfg)) {
    if (!knownKeys.has(k)) extras[k] = v;
  }
  const composePathsRaw = Array.isArray(cfg.compose_paths)
    ? (cfg.compose_paths as unknown[]).filter((x): x is string => typeof x === "string")
    : [];
  const envKeysRaw = Array.isArray(cfg.env_keys)
    ? (cfg.env_keys as unknown[]).filter((x): x is string => typeof x === "string")
    : [];
  // Apply kind defaults *only* in create mode — edit mode echoes the
  // saved row exactly so the operator sees what's actually stored.
  const defaults = initial === undefined ? defaultsFor(kind) : {};
  return {
    name: initial?.name ?? "",
    kind,
    require_2fa: initial?.require_2fa ?? false,
    cost_multiplier: String(initial?.cost_multiplier ?? 1),
    // local
    compose_path:
      typeof cfg.compose_path === "string" ? cfg.compose_path : (defaults.compose_path ?? ""),
    compose_paths_csv: composePathsRaw.join(", "),
    preview_mode:
      cfg.preview_mode === "subdomain" || cfg.preview_mode === "path"
        ? cfg.preview_mode
        : (defaults.preview_mode ?? ""),
    preview_slug: typeof cfg.preview_slug === "string" ? cfg.preview_slug : "",
    strip_prefix:
      typeof cfg.strip_prefix === "boolean"
        ? cfg.strip_prefix
          ? "true"
          : "false"
        : ((defaults.strip_prefix as "" | "true" | "false") ?? ""),
    primary_service: typeof cfg.primary_service === "string" ? cfg.primary_service : "",
    primary_port: typeof cfg.primary_port === "number" ? String(cfg.primary_port) : "",
    upstream_scheme:
      cfg.upstream_scheme === "https" || cfg.upstream_scheme === "http" ? cfg.upstream_scheme : "",
    upstream_host_header:
      typeof cfg.upstream_host_header === "string" ? cfg.upstream_host_header : "",
    upstream_tls_insecure: cfg.upstream_tls_insecure === true,
    build: cfg.build === true,
    // remote_ssh
    host: typeof cfg.host === "string" ? cfg.host : "",
    user: typeof cfg.user === "string" ? cfg.user : (defaults.user ?? ""),
    port: typeof cfg.port === "number" ? String(cfg.port) : (defaults.port ?? ""),
    key_secret_ref: typeof cfg.key_secret_ref === "string" ? cfg.key_secret_ref : "",
    remote_compose_path:
      typeof cfg.compose_path === "string" && kind === "remote_ssh"
        ? cfg.compose_path
        : (defaults.remote_compose_path ?? ""),
    // webhook
    webhook_url: typeof cfg.url === "string" ? cfg.url : "",
    webhook_secret_ref: typeof cfg.secret_ref === "string" ? cfg.secret_ref : "",
    env_keys_csv: envKeysRaw.join(", "),
    extras,
  };
}

/** Build the API payload from the form state. Strict about types so
 * the backend's pydantic discriminated union doesn't have to coerce —
 * what we POST is what gets stored. */
export function writeForm(form: FormState): {
  name: string;
  deploy_target_kind: DeployTargetKind;
  deploy_target_config: Record<string, unknown>;
  require_2fa: boolean;
  cost_multiplier: number;
} {
  const config: Record<string, unknown> = { ...form.extras };

  if (form.kind === "local") {
    if (form.compose_path.trim()) config.compose_path = form.compose_path.trim();
    const composePaths = form.compose_paths_csv
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (composePaths.length > 0) config.compose_paths = composePaths;
    if (form.preview_mode) config.preview_mode = form.preview_mode;
    if (form.preview_slug.trim()) {
      config.preview_slug = form.preview_slug.trim().toLowerCase();
    }
    if (form.strip_prefix !== "") {
      config.strip_prefix = form.strip_prefix === "true";
    }
    if (form.primary_service.trim()) config.primary_service = form.primary_service.trim();
    if (form.primary_port.trim()) {
      const n = Number.parseInt(form.primary_port, 10);
      if (Number.isFinite(n)) config.primary_port = n;
    }
    if (form.upstream_scheme) config.upstream_scheme = form.upstream_scheme;
    if (form.upstream_host_header.trim()) {
      config.upstream_host_header = form.upstream_host_header.trim();
    }
    config.upstream_tls_insecure = form.upstream_tls_insecure;
    config.build = form.build;
  } else if (form.kind === "remote_ssh") {
    if (form.host.trim()) config.host = form.host.trim();
    if (form.user.trim()) config.user = form.user.trim();
    if (form.port.trim()) {
      const n = Number.parseInt(form.port, 10);
      if (Number.isFinite(n)) config.port = n;
    }
    if (form.key_secret_ref) config.key_secret_ref = form.key_secret_ref;
    if (form.remote_compose_path.trim()) {
      config.compose_path = form.remote_compose_path.trim();
    }
  } else if (form.kind === "webhook") {
    if (form.webhook_url.trim()) config.url = form.webhook_url.trim();
    if (form.webhook_secret_ref) config.secret_ref = form.webhook_secret_ref;
    const envKeys = form.env_keys_csv
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (envKeys.length > 0) config.env_keys = envKeys;
  }

  return {
    name: form.name.trim(),
    deploy_target_kind: form.kind,
    deploy_target_config: config,
    require_2fa: form.require_2fa,
    cost_multiplier: Number(form.cost_multiplier) || 1,
  };
}
