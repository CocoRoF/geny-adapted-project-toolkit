import { type FormEvent, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { requestMagicLink } from "@/api/auth";
import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";

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

/** `/login` — the email field + "send magic link" submit. If we're
 * already signed in we redirect immediately to the projects list (or
 * back to the page the user was trying to reach). */
export function Login() {
  const { status } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState<{ delivered: boolean; devToken?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Redirect happens in an effect so it doesn't fire during render —
  // react-router's navigate is a side effect.
  useEffect(() => {
    if (status === "signed_in") {
      void navigate(resolveFromLocation(location.state), { replace: true });
    }
  }, [status, navigate, location.state]);

  if (status === "signed_in") {
    return null;
  }

  function onSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    void requestMagicLink(email)
      .then((resp) => {
        setSent(
          resp.token
            ? { delivered: resp.delivered, devToken: resp.token }
            : { delivered: resp.delivered },
        );
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        setSubmitting(false);
      });
  }

  return (
    <section className="auth-page">
      <h2>{t("auth.login.title")}</h2>
      <form onSubmit={onSubmit} className="auth-form">
        <label htmlFor="auth-email">{t("auth.login.email.label")}</label>
        <input
          id="auth-email"
          type="email"
          autoComplete="email"
          placeholder={t("auth.login.email.placeholder")}
          value={email}
          onChange={(e) => setEmail(e.currentTarget.value)}
          required
        />
        <button type="submit" disabled={submitting || email.length === 0}>
          {t("auth.login.submit")}
        </button>
      </form>
      {sent ? (
        <p className="auth-sent">
          {t("auth.login.sent")}
          {sent.devToken ? (
            <code className="auth-dev-token" data-testid="dev-magic-token">
              {sent.devToken}
            </code>
          ) : null}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="auth-error-text">
          {error}
        </p>
      ) : null}
    </section>
  );
}
