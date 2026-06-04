/** Phase N — scaffold preset listing + new-project-from-scaffold. */

import { apiFetch, apiGet } from "@/api/client";
import type { ProjectResponse } from "@/api/projects";

export type OptionType = "integer" | "string" | "boolean" | "enum";

export interface ScaffoldOption {
  id: string;
  label: string;
  type: OptionType;
  default: unknown;
  description: string;
  /** present when type === "enum" */
  choices?: string[];
  /** present when type === "integer" */
  min?: number;
  max?: number;
}

export interface ScaffoldPreset {
  id: string;
  display_name: string;
  description: string;
  stack: string[];
  icon: string;
  deploy_target_kind: string;
  option_schema: ScaffoldOption[];
}

export interface ScaffoldListResponse {
  presets: ScaffoldPreset[];
}

export interface ScaffoldRequestPayload {
  slug: string;
  display_name: string;
  repo_name: string;
  repo_visibility: "private" | "public";
  preset_id: string;
  preset_options: Record<string, unknown>;
}

export interface ScaffoldRepoInfo {
  name: string;
  full_name: string;
  html_url: string;
  clone_url: string;
  default_branch: string;
  private: boolean;
}

export interface ScaffoldSummary {
  files_created: number;
  commit_sha: string;
  preset_id: string;
}

export interface ScaffoldResponse {
  project: ProjectResponse;
  repo: ScaffoldRepoInfo;
  scaffold_summary: ScaffoldSummary;
}

export const listScaffolds = (): Promise<ScaffoldListResponse> =>
  apiGet<ScaffoldListResponse>("/_gapt/api/scaffolds");

export const createProjectFromScaffold = (
  payload: ScaffoldRequestPayload,
): Promise<ScaffoldResponse> =>
  apiFetch<ScaffoldResponse>("/_gapt/api/projects/scaffold", {
    method: "POST",
    json: payload,
  });
