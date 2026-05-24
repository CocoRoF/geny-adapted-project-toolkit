import { type FormEvent, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Mail } from "lucide-react";

import { requestMagicLink } from "@/api/auth";
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

/** `/login` — email field + "send magic link" submit. Centered card
 * on a full-bleed background. Once a link is requested we show the
 * dev token inline (dev mode prints it to the server log otherwise). */
export function Login() {
  const { status } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState<{ delivered: boolean; devToken?: string } | null>(null);
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
            <Field label={t("auth.login.email.label")}>
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
              <Mail className="h-4 w-4" />
              {t("auth.login.submit")}
            </Button>
          </form>

          {sent ? (
            <div className="mt-4 rounded-md border border-success/40 bg-success/10 px-3 py-2">
              <p className="text-[12px] text-success">{t("auth.login.sent")}</p>
              {sent.devToken ? (
                <code
                  data-testid="dev-magic-token"
                  className="mt-2 block break-all rounded bg-bg px-2 py-1.5 text-[11px] text-fg-muted"
                >
                  {sent.devToken}
                </code>
              ) : null}
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
        </div>
      </div>
    </div>
  );
}
