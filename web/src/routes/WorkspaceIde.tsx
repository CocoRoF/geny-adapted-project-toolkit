import { Link, useParams } from "react-router-dom";

import { useI18n } from "@/app/providers/i18n-context";

/** `/projects/:pid/w/:wid` — placeholder for the dockview IDE shell
 * that ships in Cycle 3.3. */
export function WorkspaceIde() {
  const { pid, wid } = useParams();
  const { t } = useI18n();
  return (
    <section className="workspace-ide">
      <Link to={`/projects/${pid ?? ""}`}>{t("nav.back_to_projects")}</Link>
      <h2>
        {t("workspace.title")} · {wid}
      </h2>
    </section>
  );
}
