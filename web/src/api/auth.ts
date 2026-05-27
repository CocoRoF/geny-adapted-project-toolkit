import { apiGet, apiPost } from "@/api/client";

export interface MeResponse {
  user_id: string;
  display_name: string | null;
  /** When false, the server treats every request as the admin and the
   * SPA can skip the login screen. Set via `GAPT_AUTH_ENABLED=false`
   * for trusted localhost-only deployments. */
  auth_enabled: boolean;
}

export interface LoginRequest {
  id: string;
  password: string;
}

export const fetchMe = () => apiGet<MeResponse>("/_gapt/api/auth/me");

export const login = (body: LoginRequest) => apiPost<void>("/_gapt/api/auth/login", body);

export const logout = () => apiPost<void>("/_gapt/api/auth/logout");
