import { useI18n } from "@/app/providers/i18n-context";
import { type ThemeMode, useTheme } from "@/app/providers/theme-context";

/** Minimal three-way theme picker — matches the language switcher
 * shape so the header reads consistently. */
export function ThemeSwitcher() {
  const { t } = useI18n();
  const { mode, setMode } = useTheme();
  return (
    <label className="theme-switcher">
      <span className="theme-switcher__label">{t("theme.label")}:</span>
      <select
        aria-label={t("theme.label")}
        value={mode}
        onChange={(e) => setMode(e.currentTarget.value as ThemeMode)}
      >
        <option value="light">{t("theme.light")}</option>
        <option value="dark">{t("theme.dark")}</option>
        <option value="system">{t("theme.system")}</option>
      </select>
    </label>
  );
}
