import { Command } from "cmdk";
import { useEffect, useMemo, useState } from "react";

import { useI18n } from "@/app/providers/i18n-context";
import { type PaletteAction, usePalette } from "@/app/providers/palette-context";

/** Renders the cmdk-backed palette dialog. Mounts at the App root so
 * any route can open it. Pulls live actions from `usePalette()`. */
export function CommandPalette() {
  const palette = usePalette();
  const { t } = useI18n();
  const [tick, setTick] = useState(0);

  useEffect(() => {
    // Re-render whenever the registry mutates.
    return palette.subscribe(() => setTick((v) => v + 1));
  }, [palette]);

  // `tick` is the version counter — it forces the memo to refresh
  // every time the registry mutates.
  const actions = useMemo(() => {
    void tick;
    return palette.list();
  }, [palette, tick]);

  const grouped = useMemo(() => {
    const buckets = new Map<string, PaletteAction[]>();
    for (const action of actions) {
      const arr = buckets.get(action.section) ?? [];
      arr.push(action);
      buckets.set(action.section, arr);
    }
    return Array.from(buckets.entries());
  }, [actions]);

  if (!palette.isOpen) return null;

  return (
    <Command.Dialog
      open={palette.isOpen}
      onOpenChange={(open) => {
        if (!open) palette.close();
      }}
      label={t("palette.open")}
      className="cmdk-dialog"
      data-testid="command-palette"
      title={t("palette.open")}
    >
      <Command.Input placeholder={t("palette.placeholder")} className="cmdk-input" />
      <Command.List className="cmdk-list">
        <Command.Empty>{t("palette.empty")}</Command.Empty>
        {grouped.map(([section, items]) => (
          <Command.Group key={section} heading={section} className="cmdk-group">
            {items.map((action) => (
              <Command.Item
                key={action.id}
                value={`${action.title} ${(action.keywords ?? []).join(" ")}`}
                onSelect={() => {
                  action.run();
                  palette.close();
                }}
                className="cmdk-item"
              >
                <span className="cmdk-item-title">{action.title}</span>
                {action.shortcut ? <kbd className="cmdk-shortcut">{action.shortcut}</kbd> : null}
              </Command.Item>
            ))}
          </Command.Group>
        ))}
      </Command.List>
      <footer className="cmdk-footer">
        <small>{t("palette.shortcut.hint")}</small>
      </footer>
    </Command.Dialog>
  );
}
