import { Link } from "react-router-dom";

import { useI18n } from "@/app/providers/i18n-context";

/** `/settings/*` — placeholder. Profile / locale / appearance / API
 * keys land in M1-E4. Cycle 3.1 ships this so deep links don't 404. */
export function Settings() {
  const { t } = useI18n();
  return (
    <section className="settings-page">
      <Link to="/projects">{t("nav.back_to_projects")}</Link>
      <h2>Settings</h2>
    </section>
  );
}
