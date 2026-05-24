import { type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { status, error } = useAuth();
  const { t } = useI18n();
  const location = useLocation();

  if (status === "idle") {
    return (
      <div className="grid h-full place-items-center">
        <div className="flex items-center gap-2 text-fg-muted">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-[13px]">{t("app.loading")}</span>
        </div>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div role="alert" className="mx-auto max-w-[600px] px-6 py-12">
        <div className="rounded-lg border border-danger/40 bg-danger/10 p-4">
          <p className="text-[13px] text-danger">{t("app.error_boundary")}</p>
          {error ? (
            <pre className="mt-3 overflow-auto rounded bg-bg-subtle p-2 text-[11px] text-fg-muted">
              {error}
            </pre>
          ) : null}
        </div>
      </div>
    );
  }
  if (status === "signed_out") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}
