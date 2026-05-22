import { t, type Locale } from "@/i18n";

interface Props {
  locale: Locale;
  onChange: (next: Locale) => void;
}

export function LanguageSwitcher({ locale, onChange }: Props) {
  return (
    <label className="lang-switcher">
      <span className="lang-switcher__label">{t("locale.label", locale)}:</span>
      <select
        aria-label={t("locale.label", locale)}
        value={locale}
        onChange={(e) => onChange(e.target.value as Locale)}
      >
        <option value="ko">{t("locale.ko", locale)}</option>
        <option value="en">{t("locale.en", locale)}</option>
      </select>
    </label>
  );
}
