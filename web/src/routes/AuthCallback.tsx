import { useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";

import { ApiError } from "@/api/client";
import { completeMagicLink } from "@/api/auth";
import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";

type State = "pending" | "ok" | "failed";

/** `/auth/callback?token=…` — magic-link completion lands here. We
 * call the callback endpoint to set the cookie, then ping `/me` again
 * so `<AuthProvider>` flips to `signed_in`, then redirect to the
 * projects list. */
export function AuthCallback() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const { t } = useI18n();
  const { refresh } = useAuth();
  const [state, setState] = useState<State>("pending");
  const [reason, setReason] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setState("failed");
      setReason("missing token");
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        await completeMagicLink(token);
        if (cancelled) return;
        await refresh();
        if (cancelled) return;
        setState("ok");
      } catch (err) {
        if (cancelled) return;
        setState("failed");
        if (err instanceof ApiError) {
          setReason(`${err.code}: ${err.reason}`);
        } else {
          setReason(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, refresh]);

  if (state === "ok") {
    return <Navigate to="/projects" replace />;
  }
  if (state === "failed") {
    return (
      <section className="auth-page" role="alert">
        <h2>{t("auth.login.callback_failed")}</h2>
        {reason ? <pre>{reason}</pre> : null}
      </section>
    );
  }
  return (
    <section className="auth-page">
      <p>{t("auth.login.callback_pending")}</p>
    </section>
  );
}
