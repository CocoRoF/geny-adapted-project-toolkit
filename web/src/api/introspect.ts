import { apiFetch, apiGet } from "@/api/client";

export type ProjectKind =
  | "nextjs"
  | "vite"
  | "express"
  | "fastapi"
  | "django"
  | "flask"
  | "go"
  | "rust"
  | "static"
  | "unknown";

/** Mirror of `IntrospectResponse` from `server/.../routers/introspect.py`.
 * Field names line up 1:1 — keep them in sync when adding fields. */
export interface IntrospectResponse {
  kind: ProjectKind;
  has_compose: boolean;
  secondary_stacks: string[];
  dev_command: string | null;
  dev_port: number | null;
  dev_cwd: string | null;
  dev_env_hints: Record<string, string>;
  test_command: string | null;
  prod_compose_path: string | null;
  prod_compose_paths: string[];
  prod_primary_service: string | null;
  prod_primary_port: number | null;
  prod_build_required: boolean;
  env_files: string[];
  env_examples: string[];
  needs_basepath: boolean;
  basepath_config_file: string | null;
  confidence: number;
  notes: string[];
  sources: string[];
}

export interface ApplyIntrospectionInput {
  create_dev_service?: boolean;
  create_prod_environment?: boolean;
  dev_label?: string;
  dev_command?: string | null;
  dev_port?: number | null;
  dev_cwd?: string | null;
  prod_environment_name?: string;
  prod_compose_path?: string | null;
  prod_compose_paths?: string[] | null;
  prod_primary_service?: string | null;
  prod_primary_port?: number | null;
  prod_build?: boolean | null;
  prod_preview_mode?: "path" | "subdomain";
}

export interface ApplyIntrospectionResponse {
  introspection: IntrospectResponse;
  created_dev_service: Record<string, unknown> | null;
  created_environment: Record<string, unknown> | null;
  actions: string[];
}

export const getIntrospection = (workspaceId: string) =>
  apiGet<IntrospectResponse>(`/api/workspaces/${workspaceId}/introspect`);

export const applyIntrospection = (
  workspaceId: string,
  body: ApplyIntrospectionInput = {},
) =>
  apiFetch<ApplyIntrospectionResponse>(
    `/api/workspaces/${workspaceId}/apply-introspection`,
    { method: "POST", json: body },
  );

export interface AutoPatchResponse {
  patched_files: string[];
  skipped: string[];
  next_steps: string[];
}

export const autoPatchNextjsBasePath = (workspaceId: string) =>
  apiFetch<AutoPatchResponse>(
    `/api/workspaces/${workspaceId}/auto-patch/nextjs-basepath`,
    { method: "POST", json: {} },
  );
