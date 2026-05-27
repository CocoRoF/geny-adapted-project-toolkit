import { apiDelete, apiFetch, apiGet } from "@/api/client";

// ─────────────────────────────────────────────── Cloudflare ──

export interface CloudflareConfig {
  account_id: string | null;
  zone_id: string | null;
  tunnel_id: string | null;
  preview_domain: string | null;
  upstream: string | null;
}

export interface CloudflareConfigResponse {
  configured: boolean;
  config: CloudflareConfig;
  verified_at: string | null;
  updated_at: string | null;
}

export interface PutCloudflareConfigRequest {
  /** Omit to leave the existing token in place. */
  api_token?: string;
  config: CloudflareConfig;
}

export interface CloudflareTokenInfo {
  id?: string;
  status?: string;
  not_before?: string;
  expires_on?: string | null;
}

export interface CloudflareAccount {
  id: string;
  name: string;
  source?: "token" | "zone";
  /** "token" = returned by /accounts directly. "zone" = derived
   *  from a zone the token can see (token lacks Account scope). */
}

export interface CloudflareTunnel {
  id: string;
  name: string;
  status: string;
  connections: number;
}

export interface CloudflareZone {
  id: string;
  name: string;
  account_id: string;
}

export interface CloudflareVerifyResponse {
  token: CloudflareTokenInfo;
  accounts: CloudflareAccount[];
  tunnels_by_account: Record<string, CloudflareTunnel[]>;
  zones: CloudflareZone[];
  warnings: string[];
}

export interface CloudflareIngressEntry {
  hostname?: string;
  service: string;
  path?: string;
  originRequest?: Record<string, unknown>;
}

export interface TunnelSnapshotResponse {
  mode: "remote_managed" | "local_config" | "unknown";
  ingress: CloudflareIngressEntry[];
  warp_routing: Record<string, unknown> | null;
  raw?: Record<string, unknown> | null;
}

export interface EnsureWildcardRequest {
  wildcard_hostname?: string;
  upstream?: string;
}

export const getCloudflareConfig = () =>
  apiGet<CloudflareConfigResponse>(`/_gapt/api/providers/cloudflare`);

export const putCloudflareConfig = (payload: PutCloudflareConfigRequest) =>
  apiFetch<CloudflareConfigResponse>(`/_gapt/api/providers/cloudflare`, {
    method: "PUT",
    json: payload,
  });

export const deleteCloudflareConfig = () =>
  apiDelete<void>(`/_gapt/api/providers/cloudflare`);

export const verifyCloudflareConfig = () =>
  apiFetch<CloudflareVerifyResponse>(`/_gapt/api/providers/cloudflare/verify`, {
    method: "POST",
    json: {},
  });

export const getCloudflareTunnelSnapshot = () =>
  apiGet<TunnelSnapshotResponse>(`/_gapt/api/providers/cloudflare/tunnel/snapshot`);

export const ensureCloudflareWildcard = (body?: EnsureWildcardRequest) =>
  apiFetch<TunnelSnapshotResponse>(
    `/_gapt/api/providers/cloudflare/tunnel/ensure-wildcard`,
    { method: "POST", json: body ?? {} },
  );

// ─────────────────── local→remote tunnel migration ──

export interface LocalInspectionResponse {
  path: string;
  exists: boolean;
  readable: boolean;
  raw_text: string;
  tunnel_id: string | null;
  tunnel_uuid: string | null;
  credentials_file: string | null;
  ingress: CloudflareIngressEntry[];
}

export interface MigrationScriptResponse {
  filename: string;
  sudo_command: string;
  script: string;
}

export interface MigrationVerifyResponse {
  ok: boolean;
  mode: "remote_managed" | "local_config" | "unknown";
  connection_summary: string;
  message: string;
}

export const inspectLocalCloudflared = () =>
  apiGet<LocalInspectionResponse>(
    `/_gapt/api/providers/cloudflare/migration/inspect-local`,
  );

export interface MigrationPushRequest {
  account_id?: string;
  tunnel_id?: string;
}

export const pushLocalToRemote = (body?: MigrationPushRequest) =>
  apiFetch<TunnelSnapshotResponse>(
    `/_gapt/api/providers/cloudflare/migration/push-to-remote`,
    { method: "POST", json: body ?? {} },
  );

export const getMigrationScript = () =>
  apiGet<MigrationScriptResponse>(
    `/_gapt/api/providers/cloudflare/migration/script`,
  );

export const getRevertScript = () =>
  apiGet<MigrationScriptResponse>(
    `/_gapt/api/providers/cloudflare/migration/revert-script`,
  );

export const verifyMigration = () =>
  apiFetch<MigrationVerifyResponse>(
    `/_gapt/api/providers/cloudflare/migration/verify`,
    { method: "POST", json: {} },
  );

export interface RunCutoverRequest {
  sudo_password?: string;
  tunnel_id?: string;
}

export interface RunCutoverResponse {
  ok: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  message: string;
}

export const runCutoverScript = (body: RunCutoverRequest) =>
  apiFetch<RunCutoverResponse>(
    `/_gapt/api/providers/cloudflare/migration/run-cutover`,
    { method: "POST", json: body },
  );

// ─────────────────────── wildcard cert helpers ──

export interface CertStatusResponse {
  zone_id: string | null;
  zone_name: string | null;
  preview_domain: string | null;
  wildcard_hostname: string | null;
  has_wildcard_cert: boolean;
  needs_acm: boolean;
  existing_covering_certs: string[];
  alternative_preview_domain: string | null;
  total_tls_enabled: boolean | null;
  total_tls_supported: boolean;
  dashboard_url: string | null;
  can_enable_via_api: boolean;
  message: string;
}

export interface EnableTotalTlsResponse {
  ok: boolean;
  message: string;
  raw: Record<string, unknown> | null;
}

export const getCertStatus = () =>
  apiGet<CertStatusResponse>(`/_gapt/api/providers/cloudflare/cert/status`);

export const enableTotalTls = (certificate_authority = "google") =>
  apiFetch<EnableTotalTlsResponse>(
    `/_gapt/api/providers/cloudflare/cert/enable-total-tls`,
    { method: "POST", json: { certificate_authority } },
  );
