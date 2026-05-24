import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileDiff, FilePlus, FileX, Pencil, RefreshCw } from "lucide-react";

import { ApiError } from "@/api/client";
import { type WorkspaceDiff, getWorkspaceDiff } from "@/api/diff";
import { useI18n } from "@/app/providers/i18n-context";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

interface Props {
  workspaceId: string;
}

const POLL_MS = 5000;

export function DiffPanel({ workspaceId }: Props) {
  const { t } = useI18n();
  const [diff, setDiff] = useState<WorkspaceDiff | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activePath, setActivePath] = useState<string | null>(null);
  const unifiedRef = useRef<HTMLPreElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getWorkspaceDiff(workspaceId);
      setDiff(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.reason : err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const fileBlocks = useMemo(() => splitUnifiedByFile(diff?.unified ?? ""), [diff]);

  const scrollToPath = (path: string) => {
    setActivePath(path);
    const el = unifiedRef.current?.querySelector(`[data-file="${cssEscape(path)}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="flex h-full flex-col bg-bg">
      <header className="flex h-8 shrink-0 items-center justify-between gap-3 border-b border-border bg-bg-elevated px-3 text-[12px]">
        <div className="flex items-center gap-2">
          <FileDiff className="h-3.5 w-3.5 text-fg-muted" />
          <span className="font-medium text-fg">{t("ide.diff.title")}</span>
          {diff && diff.files.length > 0 ? (
            <Badge tone="accent" className="text-[10px]">
              {diff.files.length}
            </Badge>
          ) : null}
          {diff?.truncated ? (
            <Badge tone="warn" className="text-[10px]">
              {t("ide.diff.truncated")}
            </Badge>
          ) : null}
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void load()}
          disabled={loading}
          title={t("ide.diff.refresh")}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </Button>
      </header>

      {error ? <p className="px-3 py-2 text-[12px] text-danger">{error}</p> : null}

      {diff && diff.files.length === 0 && !loading ? (
        <div className="grid flex-1 place-items-center text-[12px] text-fg-muted">
          {t("ide.diff.empty")}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <aside className="w-[220px] shrink-0 overflow-y-auto border-r border-border bg-bg-elevated">
            <ul className="py-1">
              {diff?.files.map((f) => (
                <li key={f.path}>
                  <button
                    type="button"
                    onClick={() => scrollToPath(f.path)}
                    className={cn(
                      "flex w-full items-center gap-1.5 px-2 py-1 text-left text-[12px] text-fg-muted hover:bg-surface-hover hover:text-fg",
                      activePath === f.path && "bg-surface-hover text-fg",
                    )}
                  >
                    <StatusIcon status={f.status} />
                    <span className="truncate font-mono text-[11px]" title={f.path}>
                      {f.path}
                    </span>
                    <span className="ml-auto shrink-0 text-[10px] tabular-nums">
                      <span className="text-success">+{f.additions}</span>{" "}
                      <span className="text-danger">-{f.deletions}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          <pre
            ref={unifiedRef}
            className="m-0 flex-1 overflow-auto px-3 py-2 font-mono text-[11.5px] leading-[1.45] text-fg"
          >
            {fileBlocks.length === 0 ? null : (
              fileBlocks.map((block) => (
                <div key={block.path} data-file={block.path} className="mb-3">
                  {block.lines.map((line, i) => (
                    <span key={i} className={cn("block whitespace-pre", lineClass(line))}>
                      {line || " "}
                    </span>
                  ))}
                </div>
              ))
            )}
          </pre>
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === "A" || status === "U") {
    return <FilePlus className="h-3.5 w-3.5 shrink-0 text-success" />;
  }
  if (status === "D") {
    return <FileX className="h-3.5 w-3.5 shrink-0 text-danger" />;
  }
  return <Pencil className="h-3.5 w-3.5 shrink-0 text-warn" />;
}

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) {
    return "text-fg-muted";
  }
  if (line.startsWith("@@")) {
    return "text-accent";
  }
  if (line.startsWith("diff --git") || line.startsWith("index ")) {
    return "text-fg-subtle";
  }
  if (line.startsWith("+")) {
    return "bg-success/10 text-success";
  }
  if (line.startsWith("-")) {
    return "bg-danger/10 text-danger";
  }
  return "text-fg-muted";
}

interface FileBlock {
  path: string;
  lines: string[];
}

/** Carve a unified diff blob into one block per file. The `diff --git
 * a/<path> b/<path>` line marks the start of each block; the path we
 * key by is `<path>` (the `b/` side) so the sidebar's `f.path` lines
 * up cleanly. */
function splitUnifiedByFile(unified: string): FileBlock[] {
  if (!unified) return [];
  const blocks: FileBlock[] = [];
  let current: FileBlock | null = null;
  for (const raw of unified.split("\n")) {
    if (raw.startsWith("diff --git ")) {
      // `diff --git a/foo b/foo` — the path can contain spaces if
      // it was a-quoted; we lean on the `b/<path>` suffix to extract
      // the new-side path. Fall back to "unknown" so we still emit a
      // block.
      const match = /^diff --git a\/(.+) b\/(.+)$/.exec(raw);
      const path = match ? match[2] : "unknown";
      current = { path, lines: [raw] };
      blocks.push(current);
      continue;
    }
    if (current) current.lines.push(raw);
  }
  return blocks;
}

function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return value.replace(/(["'\\])/g, "\\$1");
}
