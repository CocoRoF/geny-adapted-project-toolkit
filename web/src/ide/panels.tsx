import { type IDockviewPanelProps } from "dockview";
import { useEffect, useState } from "react";

import { useI18n } from "@/app/providers/i18n-context";
import { FileEditor } from "@/ide/Editor";
import { useEditorBus } from "@/ide/editor-store";
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

/** File tree panel — bridges the tree's `onOpenFile` callback into
 * the EditorBus so the editor panel (which lives under a separate
 * dockview root) picks up the request. */
export function FileTreePanel(props: IDockviewPanelProps<{ workspaceId: string }>) {
  const bus = useEditorBus();
  return (
    <div className="ide-panel-tree" data-panel-kind="tree">
      <FileTree workspaceId={props.params.workspaceId} onOpenFile={(path) => bus.emit(path)} />
    </div>
  );
}

/** Editor panel — subscribes to EditorBus so the tree can hand it
 * the path to open. Renders <FileEditor> against the live path. */
export function EditorPanel(props: IDockviewPanelProps<{ workspaceId: string }>) {
  const bus = useEditorBus();
  const [openPath, setOpenPath] = useState<string | null>(null);

  useEffect(() => {
    return bus.subscribe((path) => setOpenPath(path));
  }, [bus]);

  return (
    <div className="ide-panel-editor" data-panel-kind="editor">
      <FileEditor workspaceId={props.params.workspaceId} openPath={openPath} />
    </div>
  );
}
