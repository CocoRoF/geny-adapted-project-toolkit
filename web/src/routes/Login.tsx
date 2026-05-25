import { type FormEvent, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { LogIn } from "lucide-react";

import { ApiError } from "@/api/client";
import { login as apiLogin } from "@/api/auth";
import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";
import { Field, Input } from "@/ui/Input";

function resolveFromLocation(state: unknown): string {
  if (
    typeof state === "object" &&
    state !== null &&
    "from" in state &&
    typeof (state as { from?: unknown }).from === "string"
  ) {
    return (state as { from: string }).from;
  }
  return "/projects";
}

/** `/login` — MinIO/Jenkins-style single-admin sign-in.
 *
 * The server validates against `GAPT_ADMIN_ID` / `GAPT_ADMIN_PASSWORD`
 * (defaults `admin`/`admin`) and sets a session cookie on success.
 * When the operator sets `GAPT_AUTH_ENABLED=false` the AuthProvider
 * skips this screen entirely — see `RequireAuth`. */
export function Login() {
  const { status, refresh } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();

  const [id, setId] = useState("admin");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "signed_in") {
      void navigate(resolveFromLocation(location.state), { replace: true });
    }
  }, [status, navigate, location.state]);

  if (status === "signed_in") return null;

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    void (async () => {
      try {
        await apiLogin({ id, password });
        await refresh();
      } catch (err: unknown) {
        if (err instanceof ApiError && err.status === 401) {
          setError(t("auth.login.invalid"));
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setSubmitting(false);
      }
    })();
  }

  return (
    <div className="grid min-h-full place-items-center px-4 py-12">
      <div className="w-full max-w-[400px]">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-lg bg-accent text-[15px] font-bold text-accent-fg shadow-sm">
            G
          </div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">
            {t("auth.login.title")}
          </h1>
          <p className="mt-1 text-[13px] text-fg-muted">{t("app.title")}</p>
        </div>

        <div className="rounded-lg border border-border bg-bg-elevated p-5 shadow-sm">
          <form onSubmit={onSubmit} className="flex flex-col gap-4">
            <Field label={t("auth.login.id.label")} hint={t("auth.login.id.hint")}>
              <Input
                id="auth-id"
                autoComplete="username"
                placeholder="admin"
                value={id}
                onChange={(e) => setId(e.currentTarget.value)}
                required
                disabled={submitting}
                className="h-9"
              />
            </Field>
            <Field label={t("auth.login.password.label")}>
              <Input
                id="auth-password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.currentTarget.value)}
                required
                disabled={submitting}
                className="h-9"
              />
            </Field>
            <Button
              type="submit"
              variant="primary"
              size="lg"
              disabled={submitting || id.length === 0 || password.length === 0}
            >
              <LogIn className="h-4 w-4" />
              {submitting ? t("auth.login.completing") : t("auth.login.continue")}
            </Button>
          </form>

          {error ? (
            <p
              role="alert"
              className="mt-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
            >
              {error}
            </p>
          ) : null}

          <p className="mt-4 text-center text-[11px] text-fg-subtle">
            {t("auth.login.admin_note")}
          </p>
        </div>
      </div>
    </div>
  );
}
