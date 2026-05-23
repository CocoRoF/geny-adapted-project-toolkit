import { type ReactNode, useCallback, useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { type TreeEntry, listTree } from "@/api/files";
import { useI18n } from "@/app/providers/i18n-context";

interface Props {
  workspaceId: string;
  onOpenFile?: ((path: string) => void) | undefined;
}

type DirState =
  | { kind: "collapsed" }
  | { kind: "loading" }
  | { kind: "ready"; entries: TreeEntry[] }
  | { kind: "error"; reason: string };

interface DirCache {
  [absolutePath: string]: DirState;
}

/** A simple, lazy-expanding workspace tree.
 *
 * Each directory caches its children in `dirs` keyed by the absolute
 * workspace path. Toggling a dir issues a `GET …/tree?path=` exactly
 * once unless the user explicitly refreshes. Files are click-to-open
 * — the parent passes `onOpenFile` to wire the editor (Cycle 3.5). */
export function FileTree({ workspaceId, onOpenFile }: Props) {
  const { t } = useI18n();
  const [dirs, setDirs] = useState<DirCache>({});

  const expand = useCallback(
    async (path: string) => {
      setDirs((prev) => ({ ...prev, [path]: { kind: "loading" } }));
      try {
        const entries = await listTree(workspaceId, path);
        setDirs((prev) => ({ ...prev, [path]: { kind: "ready", entries } }));
      } catch (err) {
        const reason =
          err instanceof ApiError
            ? `${err.code}: ${err.reason}`
            : err instanceof Error
              ? err.message
              : String(err);
        setDirs((prev) => ({ ...prev, [path]: { kind: "error", reason } }));
      }
    },
    [workspaceId],
  );

  // Auto-open the root on mount / workspace change.
  useEffect(() => {
    setDirs({ "/": { kind: "loading" } });
    void expand("/");
  }, [workspaceId, expand]);

  function toggle(path: string): void {
    const current = dirs[path];
    if (!current || current.kind === "collapsed") {
      void expand(path);
      return;
    }
    if (current.kind === "ready" || current.kind === "error") {
      setDirs((prev) => ({ ...prev, [path]: { kind: "collapsed" } }));
    }
    // ignore clicks while loading
  }

  return (
    <div className="file-tree" data-workspace-id={workspaceId}>
      <DirNode
        name="/"
        path="/"
        dirs={dirs}
        toggle={toggle}
        onOpenFile={onOpenFile}
        loadingLabel={t("app.loading")}
      />
    </div>
  );
}

interface DirNodeProps {
  name: string;
  path: string;
  dirs: DirCache;
  toggle: (path: string) => void;
  onOpenFile?: ((path: string) => void) | undefined;
  loadingLabel: string;
}

function DirNode({ name, path, dirs, toggle, onOpenFile, loadingLabel }: DirNodeProps): ReactNode {
  const state = dirs[path] ?? { kind: "collapsed" };
  const open = state.kind !== "collapsed";

  return (
    <div className="file-tree-dir">
      <button
        type="button"
        className="file-tree-row file-tree-row--dir"
        aria-expanded={open}
        onClick={() => toggle(path)}
      >
        <span className="file-tree-twisty">{open ? "▾" : "▸"}</span>
        <span className="file-tree-name">{name}</span>
      </button>
      {state.kind === "loading" ? <p className="file-tree-loading">{loadingLabel}</p> : null}
      {state.kind === "error" ? (
        <p role="alert" className="file-tree-error">
          {state.reason}
        </p>
      ) : null}
      {state.kind === "ready" ? (
        <ul className="file-tree-children">
          {state.entries.map((entry) => (
            <li key={entry.path} className="file-tree-entry">
              {entry.kind === "dir" ? (
                <DirNode
                  name={entry.name}
                  path={entry.path}
                  dirs={dirs}
                  toggle={toggle}
                  onOpenFile={onOpenFile}
                  loadingLabel={loadingLabel}
                />
              ) : (
                <button
                  type="button"
                  className="file-tree-row file-tree-row--file"
                  onClick={() => onOpenFile?.(entry.path)}
                >
                  <span className="file-tree-twisty" aria-hidden="true">
                    {" "}
                  </span>
                  <span className="file-tree-name">{entry.name}</span>
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
