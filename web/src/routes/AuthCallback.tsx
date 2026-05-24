import { useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { Loader2, XCircle } from "lucide-react";

import { ApiError } from "@/api/client";
import { completeMagicLink } from "@/api/auth";
import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";

type State = "pending" | "ok" | "failed";

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

  if (state === "ok") return <Navigate to="/projects" replace />;

  if (state === "failed") {
    return (
      <div role="alert" className="grid min-h-full place-items-center px-4 py-12">
        <div className="w-full max-w-[420px] rounded-lg border border-danger/40 bg-bg-elevated p-6 text-center shadow-sm">
          <XCircle className="mx-auto mb-3 h-8 w-8 text-danger" />
          <h2 className="text-[16px] font-semibold text-fg">{t("auth.login.callback_failed")}</h2>
          {reason ? (
            <pre className="mt-3 overflow-auto rounded bg-bg-subtle p-2 text-left text-[11px] text-fg-muted">
              {reason}
            </pre>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="grid min-h-full place-items-center px-4 py-12">
      <div className="flex items-center gap-2 text-fg-muted">
        <Loader2 className="h-4 w-4 animate-spin" />
        <p className="text-[13px]">{t("auth.login.callback_pending")}</p>
      </div>
    </div>
  );
}
