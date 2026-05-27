import { apiDelete, apiFetch, apiGet, apiPost } from "@/api/client";

export type SecretOwnerScope = "system" | "project" | "environment";
export type SecretBackendName = "keyring" | "encrypted_sqlite" | "sops" | "infisical";

export interface SecretView {
  id: string;
  scope: SecretOwnerScope;
  owner_id: string;
  key_name: string;
  backend: SecretBackendName;
  created_at: string;
  rotated_at: string | null;
}

export interface StoreSecretInput {
  scope: SecretOwnerScope;
  owner_id: string;
  key_name: string;
  value: string;
}

export const listSecrets = (params?: { scope?: SecretOwnerScope; owner_id?: string }) => {
  const q = new URLSearchParams();
  if (params?.scope) q.set("scope", params.scope);
  if (params?.owner_id) q.set("owner_id", params.owner_id);
  const suffix = q.toString();
  return apiGet<SecretView[]>(`/_gapt/api/secrets${suffix ? `?${suffix}` : ""}`);
};

export const storeSecret = (input: StoreSecretInput) =>
  apiFetch<SecretView>("/_gapt/api/secrets", { method: "POST", json: input });

export const rotateSecret = (secretId: string, value: string) =>
  apiPost<SecretView>(`/_gapt/api/secrets/${secretId}/rotate`, { value });

export const deleteSecret = (secretId: string) => apiDelete<void>(`/_gapt/api/secrets/${secretId}`);
