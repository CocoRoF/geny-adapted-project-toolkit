import { Link, useParams } from "react-router-dom";

import { useI18n } from "@/app/providers/i18n-context";

/** `/projects/:pid` — placeholder. Real overview (recent activity,
 * workspaces, env list) lands in Cycle 3.2 / 3.13. */
export function ProjectDetail() {
  const { pid } = useParams();
  const { t } = useI18n();
  return (
    <section className="project-detail">
      <Link to="/projects">{t("nav.back_to_projects")}</Link>
      <h2>
        {t("projects.title")} · {pid}
      </h2>
    </section>
  );
}
