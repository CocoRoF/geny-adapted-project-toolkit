import { apiGet } from "@/api/client";

export type AuditActorType = "user" | "agent_session" | "system";
export type AuditOutcome = "ok" | "error";

export interface AuditEntry {
  id: string;
  ts: string;
  actor_type: AuditActorType;
  actor_id: string | null;
  scope: Record<string, unknown>;
  action: string;
  subject: Record<string, unknown>;
  outcome: AuditOutcome;
  duration_ms: number | null;
  exec_code: string | null;
  payload: Record<string, unknown>;
}

export interface AuditQuery {
  action_prefix?: string;
  outcome?: AuditOutcome;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export function listProjectAudit(projectId: string, query: AuditQuery = {}): Promise<AuditEntry[]> {
  const params = new URLSearchParams();
  if (query.action_prefix) params.set("action_prefix", query.action_prefix);
  if (query.outcome) params.set("outcome", query.outcome);
  if (query.since) params.set("since", query.since);
  if (query.until) params.set("until", query.until);
  if (query.limit) params.set("limit", String(query.limit));
  if (query.offset) params.set("offset", String(query.offset));
  const qs = params.toString();
  return apiGet<AuditEntry[]>(`/api/projects/${projectId}/audit${qs ? `?${qs}` : ""}`);
}
