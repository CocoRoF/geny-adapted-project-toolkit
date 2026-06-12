import { apiFetch, apiGet, apiPost } from "@/api/client";

export type AgentSessionStatus = "active" | "stale_idle" | "stale_compact" | "archived";

export interface SessionResponse {
  id: string;
  project_id: string;
  workspace_id: string;
  user_id: string;
  env_manifest_id: string;
  status: AgentSessionStatus;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  // Phase K.2 — Anthropic cache token counts. Older server responses
  // may omit them — surface as 0 on the UI when undefined.
  cache_write_tokens?: number;
  cache_read_tokens?: number;
  last_active_at: string;
  created_at: string;
  // Phase J.1 — list-view enrichments. Both default to 0 / null on
  // older clients hitting an unenriched response so the UI degrades
  // gracefully ("—" placeholders instead of crashing on undefined).
  turn_count?: number;
  first_user_message?: string | null;
}

export interface CreateSessionInput {
  workspace_id: string;
  env_id?: string;
  /** Phase G.4 — per-session manifest overrides. Each missing field
   *  falls through to the global Settings → Pipeline overrides, then
   *  to the manifest's bundled defaults. Applied at session-create
   *  time only — switching mid-conversation requires a new session. */
  model?: string;
  max_tokens?: number;
  max_iterations?: number;
  cost_budget_usd?: number;
  timeout_s?: number;
  // Phase L.4 — Anthropic extended-thinking budget. Setting
  // `thinking_budget_tokens > 0` implicitly enables thinking unless
  // `thinking_enabled` is explicitly `false`.
  thinking_enabled?: boolean;
  thinking_budget_tokens?: number;
}

export type SessionEventKind =
  | "text"
  | "tool_call"
  | "tool_result"
  | "cost"
  | "error"
  | "done"
  | "step"
  // Phase I.2 — user's own prompt for the turn. Published first by
  // `_run_with_lifecycle` so the transcript carries both sides.
  | "user_message";

export interface MessageReplayEntry {
  seq: number;
  kind: SessionEventKind;
  data: Record<string, unknown>;
  ts: string;
}

export const createSession = (
  projectId: string,
  input: CreateSessionInput,
): Promise<SessionResponse> =>
  apiPost<SessionResponse>(`/_gapt/api/projects/${projectId}/sessions`, input);

export const listSessions = (
  projectId: string,
  opts: { includeArchived?: boolean; workspaceId?: string } = {},
): Promise<SessionResponse[]> => {
  const qs = new URLSearchParams();
  if (opts.includeArchived) qs.set("include_archived", "true");
  if (opts.workspaceId) qs.set("workspace_id", opts.workspaceId);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiGet<SessionResponse[]>(`/_gapt/api/projects/${projectId}/sessions${suffix}`);
};

// Phase L.2 — flip a session back to active so the ChatPanel can
// attach to it again. Idempotent for already-active sessions.
export const reactivateSession = (sessionId: string): Promise<SessionResponse> =>
  apiPost<SessionResponse>(`/_gapt/api/sessions/${sessionId}/reactivate`);

// Phase J.2 — typed transcript shape returned by `/transcript?format=json`.
// Mirrors `gapt_server.agent.transcript.to_dict`. Used by SessionDetail.
export interface TranscriptToolUse {
  tool: string;
  tool_use_id: string | null;
  input: unknown;
  output: unknown;
  is_error: boolean;
}

export interface TranscriptTurn {
  user: string;
  assistant: string;
  cost_usd: number;
  started_at: string | null;
  tool_uses: TranscriptToolUse[];
}

export interface SessionTranscript {
  session_id: string;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  // Phase K.2 — Anthropic cache token totals. Default to 0 when
  // the server didn't emit them (older transcript responses).
  total_cache_write_tokens?: number;
  total_cache_read_tokens?: number;
  turns: TranscriptTurn[];
}

export const getSessionTranscript = (sessionId: string): Promise<SessionTranscript> =>
  apiGet<SessionTranscript>(`/_gapt/api/sessions/${sessionId}/transcript?format=json`);

export const getSession = (sessionId: string): Promise<SessionResponse> =>
  apiGet<SessionResponse>(`/_gapt/api/sessions/${sessionId}`);

export type ChatMode = "plan" | "act";

export interface InvokeOverrides {
  /** Phase M.2 — per-invoke model swap. The server mutates the
   *  pipeline's `_config.model.*` (NOT `state.*` — that gets wiped by
   *  the executor's `_init_state` on every run). Pre-M.2 the server
   *  edited `state.model` and the override appeared to work but the
   *  manifest model silently kept running; the contract is now
   *  durable until the next invoke or a `clear`. */
  model?: string | null;
  thinking_enabled?: boolean | null;
  thinking_budget_tokens?: number | null;
  /** Names of overrides to revert to the manifest baseline (captured
   *  at first override). Recognised: `"model"`, `"thinking_enabled"`,
   *  `"thinking_budget_tokens"`, plus the alias `"thinking"` which
   *  clears both thinking_* fields at once. Reset wins over a set
   *  value in the same request — the UI's reset button doesn't have
   *  to also blank the input. */
  clear?: Array<"model" | "thinking" | "thinking_enabled" | "thinking_budget_tokens"> | null;
}

export const invokeSession = (
  sessionId: string,
  message: string,
  mode: ChatMode = "act",
  overrides: InvokeOverrides = {},
): Promise<{ session_id: string; status: string }> =>
  apiPost<{ session_id: string; status: string }>(`/_gapt/api/sessions/${sessionId}/invoke`, {
    message,
    mode,
    ...(overrides.model != null ? { model: overrides.model } : {}),
    ...(overrides.thinking_enabled != null ? { thinking_enabled: overrides.thinking_enabled } : {}),
    ...(overrides.thinking_budget_tokens != null
      ? { thinking_budget_tokens: overrides.thinking_budget_tokens }
      : {}),
    ...(overrides.clear && overrides.clear.length > 0 ? { clear: overrides.clear } : {}),
  });

export const interruptSession = (
  sessionId: string,
): Promise<{ session_id: string; cancelled: boolean }> =>
  apiPost<{ session_id: string; cancelled: boolean }>(`/_gapt/api/sessions/${sessionId}/interrupt`);

/** Phase M.2 — current resolved values of the per-session overrides.
 *  Returned by `patchSessionOverrides` so the chat UI can sync its
 *  pills after a clear without inferring. */
export interface OverrideSnapshot {
  model: string | null;
  thinking_enabled: boolean | null;
  thinking_budget_tokens: number | null;
}

/** Phase M.2 — apply a per-session override or revert *immediately*
 *  (no LLM call). The chat panel's pill reset button posts
 *  `clear: ["model"]` etc. so the manifest baseline takes effect on
 *  the *next* turn without waiting for a user message. */
export const patchSessionOverrides = (
  sessionId: string,
  patch: InvokeOverrides,
): Promise<OverrideSnapshot> => {
  const body: Record<string, unknown> = {};
  if (patch.model != null) body.model = patch.model;
  if (patch.thinking_enabled != null) body.thinking_enabled = patch.thinking_enabled;
  if (patch.thinking_budget_tokens != null)
    body.thinking_budget_tokens = patch.thinking_budget_tokens;
  if (patch.clear && patch.clear.length > 0) body.clear = patch.clear;
  return apiFetch<OverrideSnapshot>(`/_gapt/api/sessions/${sessionId}/overrides`, {
    method: "PATCH",
    json: body,
  });
};

export const replaySessionMessages = (
  sessionId: string,
  since = 0,
): Promise<MessageReplayEntry[]> =>
  apiGet<MessageReplayEntry[]>(
    `/_gapt/api/sessions/${sessionId}/messages?since=${encodeURIComponent(String(since))}`,
  );

export const archiveSession = (sessionId: string): Promise<SessionResponse> =>
  apiPost<SessionResponse>(`/_gapt/api/sessions/${sessionId}/archive`);

export const streamUrl = (sessionId: string, since?: number): string => {
  const q = since !== undefined ? `?since=${encodeURIComponent(String(since))}` : "";
  return `/_gapt/api/sessions/${sessionId}/stream${q}`;
};
