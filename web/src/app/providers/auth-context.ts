import { createContext, useContext } from "react";

import type { MeResponse } from "@/api/auth";

export type AuthStatus = "idle" | "signed_in" | "signed_out" | "error";

export interface AuthSnapshot {
  status: AuthStatus;
  me: MeResponse | null;
  error: string | null;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthSnapshot | null>(null);

export function useAuth(): AuthSnapshot {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return ctx;
}
