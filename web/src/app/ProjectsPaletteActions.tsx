import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { type ProjectResponse, listProjects } from "@/api/projects";
import { type WorkspaceResponse, listAllActiveWorkspaces } from "@/api/workspaces";
import { useI18n } from "@/app/providers/i18n-context";
import { usePalette } from "@/app/providers/palette-context";

/** Phase C.2.b — register every non-archived project + active
 * workspace as a palette entry, so Cmd/Ctrl+K → type a branch or
 * project name → Enter jumps to the right place.
 *
 * Refreshes when the palette opens, so the list stays current with
 * what the user just created without us having to poll. */
export function ProjectsPaletteActions() {
  const palette = usePalette();
  const navigate = useNavigate();
  const { t } = useI18n();

  useEffect(() => {
    // Don't fetch until the palette opens — keeps the login page +
    // app boot path free of premature workspace requests.
    if (!palette.isOpen) return;
    let cancelled = false;
    const cleanups: Array<() => void> = [];

    void Promise.all([listProjects(), listAllActiveWorkspaces()])
      .then(([projects, workspaces]: [ProjectResponse[], WorkspaceResponse[]]) => {
        if (cancelled) return;
        const projectById = new Map(projects.map((p) => [p.id, p]));
        for (const p of projects) {
          if (p.archived_at) continue;
          cleanups.push(
            palette.register({
              id: `project.open.${p.id}`,
              title: t("palette.action.open_project").replace("{name}", p.display_name),
              section: t("palette.section.projects"),
              keywords: [p.slug, p.display_name, "project"],
              run: () => {
                void navigate(`/projects/${p.id}`);
              },
            }),
          );
        }
        for (const w of workspaces) {
          const proj = projectById.get(w.project_id);
          if (!proj) continue;
          cleanups.push(
            palette.register({
              id: `workspace.open.${w.id}`,
              title: t("palette.action.open_workspace")
                .replace("{branch}", w.name)
                .replace("{project}", proj.display_name),
              section: t("palette.section.workspaces"),
              keywords: [w.name, proj.slug, proj.display_name, "workspace"],
              run: () => {
                void navigate(`/projects/${w.project_id}/w/${w.id}`);
              },
            }),
          );
        }
      })
      .catch(() => {
        // Palette entries are advisory; failing silently is better
        // than blocking the user.
      });

    return () => {
      cancelled = true;
      for (const off of cleanups) off();
    };
  }, [palette, palette.isOpen, navigate, t]);

  return null;
}
