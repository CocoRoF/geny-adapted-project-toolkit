import { Monitor, Moon, Sun } from "lucide-react";

import { useI18n } from "@/app/providers/i18n-context";
import { type ThemeMode, useTheme } from "@/app/providers/theme-context";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

const MODES: {
  value: ThemeMode;
  icon: typeof Sun;
  labelKey: "theme.light" | "theme.dark" | "theme.system";
}[] = [
  { value: "light", icon: Sun, labelKey: "theme.light" },
  { value: "dark", icon: Moon, labelKey: "theme.dark" },
  { value: "system", icon: Monitor, labelKey: "theme.system" },
];

/** Segmented three-way theme picker. Each mode is a button so the
 * active state shows at a glance without opening a dropdown. */
export function ThemeSwitcher() {
  const { t } = useI18n();
  const { mode, setMode } = useTheme();
  return (
    <div
      role="radiogroup"
      aria-label={t("theme.label")}
      className="inline-flex items-center gap-0.5 rounded-md border border-border bg-bg-subtle p-0.5"
    >
      {MODES.map(({ value, icon: Icon, labelKey }) => (
        <Button
          key={value}
          variant="ghost"
          size="icon"
          aria-label={t(labelKey)}
          aria-pressed={mode === value}
          role="radio"
          aria-checked={mode === value}
          title={t(labelKey)}
          onClick={() => setMode(value)}
          className={cn(
            "h-6 w-6 rounded-[5px]",
            mode === value
              ? "bg-bg text-fg shadow-sm"
              : "text-fg-muted hover:bg-surface-hover hover:text-fg",
          )}
        >
          <Icon className="h-3.5 w-3.5" />
        </Button>
      ))}
    </div>
  );
}
