import { useCallback } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@/app/providers/auth-context";
import { useI18n } from "@/app/providers/i18n-context";
import { usePaletteAction } from "@/app/usePaletteAction";

/** Registers the top-level navigation actions inside the palette.
 * Workspace-local actions (toggle terminal, switch side view, etc.)
 * are wired directly into `IdeShell` via keyboard handlers — they
 * don't show up in the palette today. */
export function AppPaletteActions() {
  const navigate = useNavigate();
  const { t, locale, setLocale } = useI18n();
  const { signOut } = useAuth();

  const navProjects = useCallback(() => {
    void navigate("/projects");
  }, [navigate]);

  const navSettings = useCallback(() => {
    void navigate("/settings");
  }, [navigate]);

  const toggleLocale = useCallback(() => {
    setLocale(locale === "en" ? "ko" : "en");
  }, [locale, setLocale]);

  const doSignOut = useCallback(() => {
    void signOut();
  }, [signOut]);

  usePaletteAction({
    id: "navigate.projects",
    title: t("palette.action.go_projects"),
    section: t("palette.section.navigate"),
    run: navProjects,
  });
  usePaletteAction({
    id: "navigate.settings",
    title: t("palette.action.go_settings"),
    section: t("palette.section.navigate"),
    run: navSettings,
  });
  usePaletteAction({
    id: "locale.toggle",
    title: t("palette.action.toggle_locale"),
    section: t("palette.section.navigate"),
    run: toggleLocale,
  });
  usePaletteAction({
    id: "auth.sign_out",
    title: t("palette.action.sign_out"),
    section: t("palette.section.session"),
    run: doSignOut,
  });

  return null;
}
