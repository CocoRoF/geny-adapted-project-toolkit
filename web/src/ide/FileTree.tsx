import { type ReactNode, useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, File, Folder, FolderOpen } from "lucide-react";

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
  }

  return (
    <div data-workspace-id={workspaceId} className="px-1 py-1.5">
      <DirNode
        name="/"
        path="/"
        depth={0}
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
  depth: number;
  dirs: DirCache;
  toggle: (path: string) => void;
  onOpenFile?: ((path: string) => void) | undefined;
  loadingLabel: string;
}

function DirNode({
  name,
  path,
  depth,
  dirs,
  toggle,
  onOpenFile,
  loadingLabel,
}: DirNodeProps): ReactNode {
  const state = dirs[path] ?? { kind: "collapsed" };
  const open = state.kind !== "collapsed";
  const indent = depth * 12;

  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => toggle(path)}
        style={{ paddingLeft: indent + 6 }}
        className="flex w-full items-center gap-1 rounded-md py-0.5 pr-2 text-left text-[12px] text-fg-muted hover:bg-surface-hover hover:text-fg"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-fg-subtle" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-fg-subtle" />
        )}
        {open ? (
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-accent" />
        ) : (
          <Folder className="h-3.5 w-3.5 shrink-0 text-fg-muted" />
        )}
        <span className="truncate">{name}</span>
      </button>
      {state.kind === "loading" ? (
        <p style={{ paddingLeft: indent + 24 }} className="py-1 text-[11px] text-fg-subtle">
          {loadingLabel}
        </p>
      ) : null}
      {state.kind === "error" ? (
        <p
          role="alert"
          style={{ paddingLeft: indent + 24 }}
          className="py-1 text-[11px] text-danger"
        >
          {state.reason}
        </p>
      ) : null}
      {state.kind === "ready" ? (
        <div>
          {state.entries.map((entry) =>
            entry.kind === "dir" ? (
              <DirNode
                key={entry.path}
                name={entry.name}
                path={entry.path}
                depth={depth + 1}
                dirs={dirs}
                toggle={toggle}
                onOpenFile={onOpenFile}
                loadingLabel={loadingLabel}
              />
            ) : (
              <button
                key={entry.path}
                type="button"
                onClick={() => onOpenFile?.(entry.path)}
                style={{ paddingLeft: indent + 24 }}
                className="flex w-full items-center gap-1.5 rounded-md py-0.5 pr-2 text-left text-[12px] text-fg-muted hover:bg-surface-hover hover:text-fg"
              >
                <File className="h-3.5 w-3.5 shrink-0 text-fg-subtle" />
                <span className="truncate">{entry.name}</span>
              </button>
            ),
          )}
        </div>
      ) : null}
    </div>
  );
}
