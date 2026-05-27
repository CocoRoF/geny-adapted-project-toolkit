import { DiffEditor } from "@monaco-editor/react";
import { useCallback, useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import { writeFile } from "@/api/files";
import { useI18n } from "@/app/providers/i18n-context";
import { INLINE_THRESHOLD_LINES, countLines, unifiedDiff } from "@/chat/diff-util";

export interface GaptEditPayload {
  path: string;
  old: string;
  new: string;
  replaced?: number;
  all?: boolean;
}

interface Props {
  workspaceId: string;
  payload: GaptEditPayload;
}

type RevertState = "idle" | "pending" | "done" | "error";

/** Visualises a completed `gapt_edit` from the chat stream.
 *
 * The edit has already been applied (Cycle 3.6 ships the post-hoc
 * visualisation; the pre-apply approval flow is M1-E4 once the
 * backend grows a "preview" mode). Users can:
 *
 *  - toggle between inline (small changes) and side-by-side Monaco
 *    diff (larger changes)
 *  - Revert: swap old↔new and call `writeFile` with the synthesised
 *    inverse. The original buffer text isn't echoed by the tool, so
 *    we reconstruct it by replacing every occurrence of `new` with
 *    `old` inside the *current* file contents — read first, then
 *    write. */
export function DiffCard({ workspaceId, payload }: Props) {
  const { t } = useI18n();
  const [mode, setMode] = useState<"inline" | "split">("inline");
  const [revertState, setRevertState] = useState<RevertState>("idle");
  const [revertError, setRevertError] = useState<string | null>(null);

  const totalChanged = useMemo(
    () => Math.max(countLines(payload.old), countLines(payload.new)),
    [payload.old, payload.new],
  );

  const lines = useMemo(() => unifiedDiff(payload.old, payload.new), [payload.old, payload.new]);

  const revert = useCallback(() => {
    setRevertState("pending");
    setRevertError(null);
    // We don't have the surrounding file context here — reverting
    // simply writes a buffer that's `new → old` inside a 1-pass
    // string replace. The tool's `replaced` count tells us how many
    // sites to undo.
    void (async () => {
      try {
        // Round-trip: read → swap → write. Pull the current file from
        // the workspace, do an in-memory swap, push it back.
        const current = await fetch(
          `/_gapt/api/workspaces/${workspaceId}/file?path=${encodeURIComponent(payload.path)}`,
          { credentials: "include" },
        );
        if (!current.ok) {
          const body = (await current.json()) as {
            detail?: { code?: string; reason?: string };
          };
          throw new ApiError(
            current.status,
            body.detail?.code ?? `http.${current.status}`,
            body.detail?.reason ?? current.statusText,
          );
        }
        const file = (await current.json()) as { text: string; encoding: "utf-8" | "base64" };
        if (file.encoding !== "utf-8") {
          throw new ApiError(415, "diff.revert.binary", "cannot revert a binary file");
        }
        // Replace `new` with `old` — same direction as the original
        // but inverted. If `all` was true we replace every site;
        // otherwise the first one to match.
        const inverted = payload.all
          ? file.text.split(payload.new).join(payload.old)
          : file.text.replace(payload.new, payload.old);
        if (inverted === file.text) {
          throw new ApiError(409, "diff.revert.no_op", "nothing to revert");
        }
        await writeFile(workspaceId, payload.path, {
          content: inverted,
          encoding: "utf-8",
        });
        setRevertState("done");
      } catch (err) {
        setRevertState("error");
        setRevertError(
          err instanceof ApiError
            ? `${err.code}: ${err.reason}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      }
    })();
  }, [payload, workspaceId]);

  return (
    <div
      data-testid="diff-card"
      className="overflow-hidden rounded-md border border-border bg-bg-elevated"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3 py-2 text-[12px]">
        <strong className="text-fg">{t("diff.title")}</strong>
        <span className="text-fg-muted">
          {t("diff.path")}:{" "}
          <code className="rounded bg-bg-subtle px-1.5 py-0.5 text-[11px] text-fg">
            {payload.path}
          </code>
        </span>
        {typeof payload.replaced === "number" ? (
          <span className="text-fg-subtle">
            {t("diff.replaced").replace("{count}", String(payload.replaced))}
          </span>
        ) : null}
      </header>

      <div className="bg-bg-subtle">
        {totalChanged > INLINE_THRESHOLD_LINES ? (
          mode === "split" ? (
            <div className="overflow-hidden">
              <DiffEditor
                height="320px"
                original={payload.old}
                modified={payload.new}
                language="plaintext"
                theme="vs-dark"
                options={{
                  readOnly: true,
                  renderSideBySide: true,
                  minimap: { enabled: false },
                  scrollBeyondLastLine: false,
                  fontSize: 12,
                }}
              />
            </div>
          ) : (
            <InlineDiff lines={lines} />
          )
        ) : (
          <InlineDiff lines={lines} />
        )}
      </div>

      <footer className="flex items-center justify-end gap-1.5 border-t border-border px-3 py-2">
        {totalChanged > INLINE_THRESHOLD_LINES ? (
          <button
            type="button"
            onClick={() => setMode((m) => (m === "inline" ? "split" : "inline"))}
            className="h-7 rounded-md border border-border bg-surface px-2.5 text-[11px] font-medium text-fg-muted hover:bg-surface-hover hover:text-fg"
          >
            {mode === "inline" ? t("diff.show_full") : t("diff.show_inline")}
          </button>
        ) : null}
        <button
          type="button"
          onClick={revert}
          disabled={revertState === "pending" || revertState === "done"}
          className="h-7 rounded-md border border-border bg-surface px-2.5 text-[11px] font-medium text-fg-muted hover:bg-surface-hover hover:text-fg disabled:opacity-50"
        >
          {revertState === "pending"
            ? t("diff.reverting")
            : revertState === "done"
              ? t("diff.revert_done")
              : t("diff.revert")}
        </button>
        {revertError ? (
          <span role="alert" className="text-[11px] text-danger">
            {revertError}
          </span>
        ) : null}
      </footer>
    </div>
  );
}

function InlineDiff({ lines }: { lines: { removed: string[]; added: string[] } }) {
  return (
    <div data-testid="diff-inline" className="max-h-80 overflow-auto p-2 text-[12px]">
      <pre className="m-0 font-mono text-danger">
        {lines.removed.map((line, idx) => (
          <div key={`del-${String(idx)}`} className="leading-tight">
            - {line}
          </div>
        ))}
      </pre>
      <pre className="m-0 font-mono text-success">
        {lines.added.map((line, idx) => (
          <div key={`add-${String(idx)}`} className="leading-tight">
            + {line}
          </div>
        ))}
      </pre>
    </div>
  );
}
