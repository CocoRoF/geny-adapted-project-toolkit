import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { type MeResponse, fetchMe, logout as apiLogout } from "@/api/auth";
import { AuthContext, type AuthSnapshot, type AuthStatus } from "@/app/providers/auth-context";

/** Authentication state machine.
 *
 * - `idle`     — initial mount, /me poll hasn't returned
 * - `signed_in` — `/me` succeeded; `me` is the current user
 * - `signed_out` — `/me` returned 401 or the user signed out
 * - `error`    — non-401 failure (network down etc.) — surfaces banner
 *
 * Components consume the snapshot via `useAuth()` from
 * `auth-context.ts`. Routes that require a signed-in user delegate to
 * `<RequireAuth />`. */

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("idle");
  const [me, setMe] = useState<MeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (inflight.current) return inflight.current;
    const task = (async () => {
      try {
        const body = await fetchMe();
        setMe(body);
        setStatus("signed_in");
        setError(null);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          setMe(null);
          setStatus("signed_out");
          setError(null);
        } else {
          setMe(null);
          setStatus("error");
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        inflight.current = null;
      }
    })();
    inflight.current = task;
    return task;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // 401 here is fine — the cookie is already gone.
    }
    setMe(null);
    setStatus("signed_out");
    setError(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const snapshot: AuthSnapshot = useMemo(
    () => ({ status, me, error, refresh, signOut }),
    [status, me, error, refresh, signOut],
  );

  return <AuthContext.Provider value={snapshot}>{children}</AuthContext.Provider>;
}
