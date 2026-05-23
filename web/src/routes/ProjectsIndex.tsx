import { useI18n } from "@/app/providers/i18n-context";

/** `/projects` — Cycle 3.1 ships a placeholder so the router has a
 * concrete destination and `<RequireAuth>` works end-to-end. The
 * actual project list + create flow lands in Cycle 3.2. */
export function ProjectsIndex() {
  const { t } = useI18n();
  return (
    <section className="projects-index">
      <h2>{t("projects.title")}</h2>
      <p>{t("projects.empty")}</p>
    </section>
  );
}
