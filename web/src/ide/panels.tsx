import { type IDockviewPanelProps } from "dockview";

import { useI18n } from "@/app/providers/i18n-context";
import { FileTree } from "@/ide/FileTree";

/** Placeholder panel — used by every leaf that hasn't shipped yet.
 * Cycles 3.4–3.10 replace `params.kind` matches with the real
 * implementation one at a time. */
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

/** File tree panel — wraps `<FileTree>` with the dockview panel
 * contract. The workspaceId lives in `params.workspaceId`. */
export function FileTreePanel(
  props: IDockviewPanelProps<{ workspaceId: string; onOpenFile?: (path: string) => void }>,
) {
  return (
    <div className="ide-panel-tree" data-panel-kind="tree">
      <FileTree workspaceId={props.params.workspaceId} onOpenFile={props.params.onOpenFile} />
    </div>
  );
}
