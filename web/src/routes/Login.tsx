import { type FormEvent, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Loader2, LogIn, Mail } from "lucide-react";

import { completeMagicLink, requestMagicLink } from "@/api/auth";
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

/** `/login` — single-step sign-in for dev/staging environments.
 *
 * Backend mints a magic-link token and (when not in prod) returns it
 * in the response. The SPA immediately consumes the token to set the
 * session cookie, then redirects to `/projects` — effectively a
 * password-less signup + login in one click.
 *
 * In prod the server hides the token and the user has to consume
 * the emailed link manually (SMTP wiring lands in M2). */
export function Login() {
  const { status, refresh } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [phase, setPhase] = useState<"idle" | "completing" | "emailed">("idle");
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
        const resp = await requestMagicLink(email);
        const devToken = resp.dev_token ?? resp.token;
        if (devToken) {
          // Dev path: consume the token immediately. AuthProvider
          // then flips to signed_in via the /me poll and the useEffect
          // above navigates to /projects.
          setPhase("completing");
          await completeMagicLink(devToken);
          await refresh();
        } else {
          setPhase("emailed");
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
        setPhase("idle");
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
            <Field
              label={t("auth.login.email.label")}
              hint={t("auth.login.email.hint")}
            >
              <Input
                id="auth-email"
                type="email"
                autoComplete="email"
                placeholder={t("auth.login.email.placeholder")}
                value={email}
                onChange={(e) => setEmail(e.currentTarget.value)}
                required
                disabled={submitting}
                className="h-9"
              />
            </Field>
            <Button
              type="submit"
              variant="primary"
              size="lg"
              disabled={submitting || email.length === 0}
            >
              {phase === "completing" ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("auth.login.completing")}
                </>
              ) : (
                <>
                  <LogIn className="h-4 w-4" />
                  {t("auth.login.continue")}
                </>
              )}
            </Button>
          </form>

          {phase === "emailed" ? (
            <div className="mt-4 flex items-start gap-2 rounded-md border border-success/40 bg-success/10 px-3 py-2.5">
              <Mail className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
              <p className="text-[12px] text-success">{t("auth.login.sent")}</p>
            </div>
          ) : null}

          {error ? (
            <p
              role="alert"
              className="mt-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger"
            >
              {error}
            </p>
          ) : null}

          <p className="mt-4 text-center text-[11px] text-fg-subtle">
            {t("auth.login.passwordless_note")}
          </p>
        </div>
      </div>
    </div>
  );
}
