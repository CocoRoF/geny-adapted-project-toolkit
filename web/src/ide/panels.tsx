import { type IDockviewPanelProps } from "dockview";

import { useI18n } from "@/app/providers/i18n-context";

/** Placeholder panel components — each Cycle (3.4–3.10) swaps the
 * matching `params.kind` for its real implementation. */
export function PanelPlaceholder(props: IDockviewPanelProps<{ kind: string }>) {
  const { t } = useI18n();
  const kind = props.params.kind;
  return (
    <div className="ide-panel-placeholder" data-panel-kind={kind}>
      <h3>{kind}</h3>
      <p>{t("ide.placeholder")}</p>
    </div>
  );
}
