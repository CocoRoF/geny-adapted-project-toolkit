import { apiGet } from "@/api/client";

export interface CostSummaryRow {
  project_id: string;
  project_slug: string;
  project_display_name: string;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  session_count: number;
}

export interface CostSummary {
  rows: CostSummaryRow[];
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
}

export interface DailyCostRow {
  date: string;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  session_count: number;
}

export interface CostWindow {
  since?: string;
  until?: string;
}

function buildWindow(window: CostWindow): string {
  const params = new URLSearchParams();
  if (window.since) params.set("since", window.since);
  if (window.until) params.set("until", window.until);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function getCostSummary(window: CostWindow = {}): Promise<CostSummary> {
  return apiGet<CostSummary>(`/_gapt/api/cost/summary${buildWindow(window)}`);
}

export function getProjectCostDaily(
  projectId: string,
  window: CostWindow = {},
): Promise<DailyCostRow[]> {
  return apiGet<DailyCostRow[]>(
    `/_gapt/api/projects/${projectId}/cost/daily${buildWindow(window)}`,
  );
}
