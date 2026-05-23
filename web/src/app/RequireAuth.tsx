import { type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";

/** Gate every authenticated route. The initial `/me` poll holds in an
 * `idle` state — we surface a small "Loading…" line instead of
 * flickering to `/login` and back. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status, error } = useAuth();
  const { t } = useI18n();
  const location = useLocation();

  if (status === "idle") {
    return <p className="auth-pending">{t("app.loading")}</p>;
  }
  if (status === "error") {
    return (
      <div role="alert" className="auth-error">
        <p>{t("app.error_boundary")}</p>
        {error ? <pre>{error}</pre> : null}
      </div>
    );
  }
  if (status === "signed_out") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}
