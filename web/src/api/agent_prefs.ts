import { apiFetch, apiGet } from "@/api/client";

export interface AgentPrefs {
  id?: string | null;
  model?: string | null;
  max_tokens?: number | null;
  max_iterations?: number | null;
  cost_budget_usd?: number | null;
  timeout_s?: number | null;
  updated_at?: string | null;
}

export const getAgentPrefs = () => apiGet<AgentPrefs>("/api/agent-prefs");

export const putAgentPrefs = (prefs: AgentPrefs) =>
  apiFetch<AgentPrefs>("/api/agent-prefs", { method: "PUT", json: prefs });
