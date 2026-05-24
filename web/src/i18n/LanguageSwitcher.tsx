import { t, type Locale } from "@/i18n";
import { Select } from "@/ui/Input";

interface Props {
  locale: Locale;
  onChange: (next: Locale) => void;
}

export function LanguageSwitcher({ locale, onChange }: Props) {
  return (
    <Select
      aria-label={t("locale.label", locale)}
      value={locale}
      onChange={(e) => onChange(e.target.value as Locale)}
      className="h-7 w-[88px] text-[12px]"
    >
      <option value="ko">{t("locale.ko", locale)}</option>
      <option value="en">{t("locale.en", locale)}</option>
    </Select>
  );
}
