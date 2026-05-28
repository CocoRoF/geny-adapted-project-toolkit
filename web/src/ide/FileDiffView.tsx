import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";

import { ApiError } from "@/api/client";
import { type WorkspaceDiff, getWorkspaceDiff } from "@/api/diff";
import { useI18n } from "@/app/providers/i18n-context";
import { Button } from "@/ui/Button";
import { cn } from "@/ui/cn";

interface Props {
  workspaceId: string;
  path: string;
}

const POLL_MS = 5000;

/** Phase F — single-file unified-diff view shown inside the editor
 *  column. Replaces the right-hand pane of `GitPanel` + the
 *  bottom-panel "Diff" tab: clicking a changed file in the source
 *  control sidebar now opens its diff here, the same way VSCode
 *  routes the click into the main editor pane.
 *
 *  The hunks come from `GET /workspaces/<wid>/diff`'s `unified`
 *  blob — that endpoint returns the whole workspace's diff in one
 *  shot, so we filter the block matching `path` locally instead of
 *  asking the server for a per-file slice. Cheap (the unified diff
 *  is already bounded in the backend) and consistent with what the
 *  git sidebar shows. */
export function FileDiffView({ workspaceId, path }: Props) {
  const { t } = useI18n();
  const [diff, setDiff] = useState<WorkspaceDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDiff(await getWorkspaceDiff(workspaceId));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.reason
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void load();
    // Light polling so the view reflects new saves without manual
    // refresh. Matches `DiffPanel`'s old cadence.
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const lines = useMemo(
    () => extractFileBlock(diff?.unified ?? "", path),
    [diff, path],
  );
  const fileStat = useMemo(
    () => diff?.files.find((f) => f.path === path) ?? null,
    [diff, path],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-7 shrink-0 items-center gap-2 border-b border-border bg-bg-subtle px-3 text-[11px] text-fg-muted">
        {fileStat ? (
          <span className="tabular-nums">
            <span className="text-success">+{fileStat.additions}</span>{" "}
            <span className="text-danger">-{fileStat.deletions}</span>{" "}
            <span className="ml-1 text-fg-subtle">[{fileStat.status}]</span>
          </span>
        ) : (
          <span className="text-fg-subtle">
            {loading
              ? t("ide.file_diff.loading")
              : t("ide.file_diff.no_changes")}
          </span>
        )}
        <span className="ml-auto" />
        <Button
          variant="ghost"
          size="sm"
          onClick={load}
          disabled={loading}
          title={t("ide.file_diff.refresh")}
        >
          <RefreshCw className={loading ? "h-3 w-3 animate-spin" : "h-3 w-3"} />
        </Button>
      </header>
      {error ? (
        <p role="alert" className="px-3 py-2 text-[11px] text-danger">
          {error}
        </p>
      ) : null}
      <pre className="m-0 flex-1 overflow-auto px-3 py-2 font-mono text-[11.5px] leading-[1.45] text-fg">
        {lines.length === 0 && !loading && !error ? (
          <span className="text-fg-subtle">{t("ide.file_diff.no_changes")}</span>
        ) : (
          lines.map((line, i) => (
            <span key={i} className={cn("block whitespace-pre", lineClass(line))}>
              {line || " "}
            </span>
          ))
        )}
      </pre>
    </div>
  );
}

/** Pull the `diff --git a/<path> b/<path>` block matching `target`
 *  out of the unified diff blob. Returns `[]` when the file isn't in
 *  the diff (e.g. user clicked a file that hasn't actually been
 *  modified — possible in race with the 5s poll cadence). */
function extractFileBlock(unified: string, target: string): string[] {
  if (!unified) return [];
  const lines: string[] = [];
  let collecting = false;
  for (const raw of unified.split("\n")) {
    if (raw.startsWith("diff --git ")) {
      const match = /^diff --git a\/(.+) b\/(.+)$/.exec(raw);
      const path = match ? match[2] : null;
      collecting = path === target;
      if (collecting) lines.push(raw);
      continue;
    }
    if (collecting) lines.push(raw);
  }
  return lines;
}

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "text-fg-muted";
  if (line.startsWith("@@")) return "text-accent";
  if (line.startsWith("diff --git") || line.startsWith("index ")) {
    return "text-fg-subtle";
  }
  if (line.startsWith("+")) return "bg-success/10 text-success";
  if (line.startsWith("-")) return "bg-danger/10 text-danger";
  return "text-fg-muted";
}
