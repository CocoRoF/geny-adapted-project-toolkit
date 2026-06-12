import { apiDelete, apiGet, apiPost } from "@/api/client";

// ─────────────────────────────────────── health grid ──

export type ProviderState = "ok" | "missing" | "expired" | "unreachable" | "unknown";

export interface ProviderHealth {
  provider: string;
  label: string;
  kind: "api" | "cli";
  state: ProviderState;
  detail: string;
  env_var: string | null;
  binary_path: string | null;
  binary_version: string | null;
  auth_method: string | null;
  expires_at_ms: number | null;
}

export interface BackendsHealthResponse {
  providers: ProviderHealth[];
}

export const getBackendsHealth = () =>
  apiGet<BackendsHealthResponse>(`/_gapt/api/llm-backends/health`);

export const recheckClaudeCode = () =>
  apiPost<ProviderHealth>(`/_gapt/api/llm-backends/cli/claude-code/recheck`);

// ────────────────────────────────── Claude Code auth ──

export interface AuthStatusResponse {
  raw: Record<string, unknown>;
  logged_in: boolean | null;
  auth_method: string | null;
  subscription_type: string | null;
  email: string | null;
}

export const getClaudeAuthStatus = () =>
  apiGet<AuthStatusResponse>(`/_gapt/api/llm-backends/cli/claude-code/auth/status`);

export interface StartLoginRequest {
  use_console?: boolean;
  email?: string;
}

export interface StartLoginResponse {
  job_id: string;
  kind: string;
  argv: string[];
  hint: string;
}

export const startClaudeAuthLogin = (body: StartLoginRequest = {}) =>
  apiPost<StartLoginResponse>(`/_gapt/api/llm-backends/cli/claude-code/auth/login`, body);

export const claudeAuthLogout = () =>
  apiPost<{ ok: boolean; stdout: string; stderr: string }>(
    `/_gapt/api/llm-backends/cli/claude-code/auth/logout`,
  );

export interface TestConnectionResponse {
  ok: boolean;
  duration_ms: number;
  detail: string;
  raw_stdout_tail: string | null;
  raw_stderr_tail: string | null;
}

export const testClaudeConnection = () =>
  apiPost<TestConnectionResponse>(`/_gapt/api/llm-backends/cli/claude-code/test`);

// ───────────────────────────────────── auth job ─────

/** Each SSE frame from the auth-job stream. `channel` is one of
 *  `stdout` / `stderr` / `stdin` / `exit`. */
export interface AuthJobEvent {
  channel: "stdout" | "stderr" | "stdin" | "exit";
  text: string;
  ts: number;
  exit_code?: number;
}

export interface AuthJobSnapshot {
  job_id: string;
  kind: string;
  argv: string[];
  started_at: number;
  finished_at: number | null;
  exit_code: number | null;
  history: AuthJobEvent[];
}

export const getAuthJobSnapshot = (jobId: string) =>
  apiGet<AuthJobSnapshot>(`/_gapt/api/llm-backends/auth/jobs/${encodeURIComponent(jobId)}`);

export const cancelAuthJob = (jobId: string) =>
  apiPost<{ ok: boolean; killed: boolean; already_finished: boolean }>(
    `/_gapt/api/llm-backends/auth/jobs/${encodeURIComponent(jobId)}/cancel`,
  );

export const submitAuthJobInput = (jobId: string, text: string) =>
  apiPost<{ ok: boolean }>(`/_gapt/api/llm-backends/auth/jobs/${encodeURIComponent(jobId)}/input`, {
    text,
  });

/** SSE URL the modal subscribes to. Caller uses the native
 *  `EventSource` constructor with `withCredentials: true`. */
export const authJobEventsUrl = (jobId: string): string =>
  `/_gapt/api/llm-backends/auth/jobs/${encodeURIComponent(jobId)}/events`;

// ───────────────────────────────── credentials store ──

export interface StoredKeyResponse {
  provider: string;
  key_name: string;
  stored: boolean;
}

export const storeProviderApiKey = (provider: string, api_key: string) =>
  apiPost<StoredKeyResponse>(`/_gapt/api/llm-backends/api-keys/${encodeURIComponent(provider)}`, {
    api_key,
  });

export const deleteProviderApiKey = (provider: string) =>
  apiDelete<StoredKeyResponse>(`/_gapt/api/llm-backends/api-keys/${encodeURIComponent(provider)}`);

export const storeClaudeSetupToken = (token: string) =>
  apiPost<StoredKeyResponse>(`/_gapt/api/llm-backends/cli/claude-code/setup-token`, { token });
