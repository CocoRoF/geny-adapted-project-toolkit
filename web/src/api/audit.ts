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

function buildQuery(query: AuditQuery): string {
  const params = new URLSearchParams();
  if (query.action_prefix) params.set("action_prefix", query.action_prefix);
  if (query.outcome) params.set("outcome", query.outcome);
  if (query.since) params.set("since", query.since);
  if (query.until) params.set("until", query.until);
  if (query.limit) params.set("limit", String(query.limit));
  if (query.offset) params.set("offset", String(query.offset));
  return params.toString();
}

export function listProjectAudit(projectId: string, query: AuditQuery = {}): Promise<AuditEntry[]> {
  const qs = buildQuery(query);
  return apiGet<AuditEntry[]>(`/_gapt/api/projects/${projectId}/audit${qs ? `?${qs}` : ""}`);
}

/** Returns the URL pointing at the export endpoint — the UI usually
 * navigates the browser to it (so the file download triggers) rather
 * than fetching it as JSON. */
export function exportProjectAuditUrl(
  projectId: string,
  format: "csv" | "jsonl",
  query: Omit<AuditQuery, "limit" | "offset"> = {},
): string {
  const params = new URLSearchParams();
  params.set("format", format);
  if (query.action_prefix) params.set("action_prefix", query.action_prefix);
  if (query.outcome) params.set("outcome", query.outcome);
  if (query.since) params.set("since", query.since);
  if (query.until) params.set("until", query.until);
  return `/_gapt/api/projects/${projectId}/audit/export?${params.toString()}`;
}
