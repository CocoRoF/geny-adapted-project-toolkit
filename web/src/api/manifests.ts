import { apiGet } from "@/api/client";

/** Phase G.3 — manifest picker in ChatPanel.
 *
 * `id` is what gets sent back as `CreateSessionRequest.env_id`.
 * `source` lets the picker render a small badge ("workspace
 * override") so the operator knows their .gapt/manifests/foo.json
 * is winning over the bundled copy. */
export interface ManifestSummary {
  id: string;
  display_name: string;
  description: string | null;
  provider: string | null;
  model: string | null;
  source: "bundled" | "workspace";
  tags: string[];
}

export interface ManifestListResponse {
  manifests: ManifestSummary[];
  default_manifest_id: string;
}

export const listManifests = (workspaceId?: string) => {
  const qs = workspaceId
    ? `?workspace_id=${encodeURIComponent(workspaceId)}`
    : "";
  return apiGet<ManifestListResponse>(`/_gapt/api/manifests${qs}`);
};

export const getManifestDetail = (
  manifestId: string,
  workspaceId?: string,
) => {
  const qs = workspaceId
    ? `?workspace_id=${encodeURIComponent(workspaceId)}`
    : "";
  return apiGet<{ source: string; manifest: Record<string, unknown> }>(
    `/_gapt/api/manifests/${encodeURIComponent(manifestId)}${qs}`,
  );
};
