import { Command } from "cmdk";
import { useEffect, useMemo, useState } from "react";

import { useI18n } from "@/app/providers/i18n-context";
import { type PaletteAction, usePalette } from "@/app/providers/palette-context";

/** Renders the cmdk-backed palette dialog. */
export function CommandPalette() {
  const palette = usePalette();
  const { t } = useI18n();
  const [tick, setTick] = useState(0);

  useEffect(() => palette.subscribe(() => setTick((v) => v + 1)), [palette]);

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
      title={t("palette.open")}
      data-testid="command-palette"
      className="fixed inset-0 z-50 grid place-items-start justify-center bg-black/60 p-4 pt-[15vh] backdrop-blur-sm"
    >
      <div className="w-full max-w-[560px] overflow-hidden rounded-lg border border-border bg-bg-elevated shadow-2xl">
        <Command.Input
          placeholder={t("palette.placeholder")}
          className="w-full border-b border-border bg-transparent px-4 py-3 text-[14px] text-fg placeholder:text-fg-subtle focus:outline-none"
        />
        <Command.List className="max-h-[420px] overflow-y-auto py-2">
          <Command.Empty className="px-4 py-6 text-center text-[12px] text-fg-muted">
            {t("palette.empty")}
          </Command.Empty>
          {grouped.map(([section, items]) => (
            <Command.Group
              key={section}
              heading={section}
              className="px-2 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-fg-subtle"
            >
              {items.map((action) => (
                <Command.Item
                  key={action.id}
                  value={`${action.title} ${(action.keywords ?? []).join(" ")}`}
                  onSelect={() => {
                    action.run();
                    palette.close();
                  }}
                  className="flex cursor-pointer items-center justify-between gap-2 rounded-md px-2 py-1.5 text-[13px] text-fg aria-selected:bg-accent/15 aria-selected:text-accent"
                >
                  <span>{action.title}</span>
                  {action.shortcut ? (
                    <kbd className="rounded border border-border bg-bg-subtle px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">
                      {action.shortcut}
                    </kbd>
                  ) : null}
                </Command.Item>
              ))}
            </Command.Group>
          ))}
        </Command.List>
        <footer className="border-t border-border bg-bg px-3 py-1.5">
          <small className="text-[10px] text-fg-subtle">{t("palette.shortcut.hint")}</small>
        </footer>
      </div>
    </Command.Dialog>
  );
}
