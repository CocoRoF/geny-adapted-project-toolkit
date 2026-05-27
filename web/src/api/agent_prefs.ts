import { apiFetch, apiGet } from "@/api/client";

export type PermissionMode = "bypassPermissions" | "acceptEdits" | "default" | "plan";

export interface AgentPrefs {
  id?: string | null;
  model?: string | null;
  max_tokens?: number | null;
  max_iterations?: number | null;
  cost_budget_usd?: number | null;
  timeout_s?: number | null;
  permission_mode?: PermissionMode | null;
  updated_at?: string | null;
}

export const getAgentPrefs = () => apiGet<AgentPrefs>("/_gapt/api/agent-prefs");

export const putAgentPrefs = (prefs: AgentPrefs) =>
  apiFetch<AgentPrefs>("/_gapt/api/agent-prefs", { method: "PUT", json: prefs });
