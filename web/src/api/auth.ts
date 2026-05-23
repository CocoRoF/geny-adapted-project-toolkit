import { apiFetch, apiGet, apiPost } from "@/api/client";

export interface OrgMembershipSummary {
  org_id: string;
  org_slug: string;
  role: string;
}

export interface MeResponse {
  user_id: string;
  email: string;
  display_name: string | null;
  orgs: OrgMembershipSummary[];
}

export interface MagicLinkResponse {
  delivered: boolean;
  token?: string; // dev-only — surfaced when SMTP isn't wired
}

export interface MagicLinkCallback {
  user_id: string;
  email: string;
}

export const fetchMe = () => apiGet<MeResponse>("/api/auth/me");

export const requestMagicLink = (email: string) =>
  apiPost<MagicLinkResponse>("/api/auth/magic-link", { email });

export const completeMagicLink = (token: string) =>
  apiFetch<MagicLinkCallback>(`/api/auth/magic-link/callback?token=${encodeURIComponent(token)}`, {
    method: "GET",
  });

export const logout = () => apiPost<void>("/api/auth/logout");
