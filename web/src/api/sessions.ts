import { apiGet, apiPost } from "@/api/client";

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
  last_active_at: string;
  created_at: string;
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
}

export type SessionEventKind =
  | "text"
  | "tool_call"
  | "tool_result"
  | "cost"
  | "error"
  | "done"
  | "step";

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

export const listSessions = (projectId: string): Promise<SessionResponse[]> =>
  apiGet<SessionResponse[]>(`/_gapt/api/projects/${projectId}/sessions`);

export const getSession = (sessionId: string): Promise<SessionResponse> =>
  apiGet<SessionResponse>(`/_gapt/api/sessions/${sessionId}`);

export type ChatMode = "plan" | "act";

export const invokeSession = (
  sessionId: string,
  message: string,
  mode: ChatMode = "act",
): Promise<{ session_id: string; status: string }> =>
  apiPost<{ session_id: string; status: string }>(`/_gapt/api/sessions/${sessionId}/invoke`, {
    message,
    mode,
  });

export const interruptSession = (
  sessionId: string,
): Promise<{ session_id: string; cancelled: boolean }> =>
  apiPost<{ session_id: string; cancelled: boolean }>(`/_gapt/api/sessions/${sessionId}/interrupt`);

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
