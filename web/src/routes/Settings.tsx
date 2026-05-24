import { Link } from "react-router-dom";
import { ChevronLeft, Settings as SettingsIcon } from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/Card";

/** `/settings/*` — landing skeleton. Real subpages (profile / locale /
 * webhooks / API tokens) land in M1-E4. */
export function Settings() {
  const { t } = useI18n();
  return (
    <div className="mx-auto max-w-[720px] px-6 py-8">
      <Link
        to="/projects"
        className="mb-3 inline-flex items-center gap-1 text-[12px] text-fg-muted hover:text-fg"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
        {t("nav.back_to_projects")}
      </Link>
      <header className="mb-6 flex items-center gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-bg-subtle">
          <SettingsIcon className="h-4 w-4 text-fg-muted" />
        </div>
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">{t("nav.settings")}</h1>
          <p className="text-[12px] text-fg-muted">
            Profile, integrations, and per-project tokens (more arriving in M2).
          </p>
        </div>
      </header>
      <Card>
        <CardHeader>
          <CardTitle>Coming soon</CardTitle>
          <CardDescription>
            Per-user webhook subscriptions, API tokens, and theme overrides land in M2. Until then,
            global notification channels live in <code>GAPT_SLACK_WEBHOOK_URL</code> /
            <code> GAPT_DISCORD_WEBHOOK_URL</code>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-[12px] text-fg-muted">
            Theme + language can be changed from the header switchers.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
