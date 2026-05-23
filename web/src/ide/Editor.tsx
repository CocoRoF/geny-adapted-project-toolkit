import Editor, { type OnMount } from "@monaco-editor/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { readFile, writeFile } from "@/api/files";
import { useI18n } from "@/app/providers/i18n-context";

interface Props {
  workspaceId: string;
  openPath: string | null;
}

const LANG_BY_EXT: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  json: "json",
  md: "markdown",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  sh: "shell",
  bash: "shell",
  rs: "rust",
  go: "go",
  java: "java",
  kt: "kotlin",
  swift: "swift",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  c: "c",
  h: "c",
  hpp: "cpp",
  css: "css",
  scss: "scss",
  html: "html",
  sql: "sql",
};

function languageFor(path: string): string {
  const dot = path.lastIndexOf(".");
  if (dot < 0) return "plaintext";
  const ext = path.slice(dot + 1).toLowerCase();
  return LANG_BY_EXT[ext] ?? "plaintext";
}

type DocStatus = "clean" | "dirty" | "saving" | "saved" | "error";

interface DocState {
  path: string;
  encoding: "utf-8" | "base64";
  text: string;
  status: DocStatus;
  errorReason: string | null;
}

const AUTOSAVE_DEBOUNCE_MS = 300;

/** Monaco-backed file editor with debounced autosave.
 *
 * Receives the open file path from the parent (the dockview tree
 * panel routes click events through `EditorBus`). Loads the file via
 * `GET /api/workspaces/{wid}/file?path=`, edits in Monaco, and PUTs
 * the buffer 300 ms after the last keystroke. Binary files are
 * surfaced with a non-editable notice; the user opens them from a
 * terminal. */
export function FileEditor({ workspaceId, openPath }: Props) {
  const { t } = useI18n();
  const [doc, setDoc] = useState<DocState | null>(null);
  const [loading, setLoading] = useState(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load the file whenever `openPath` changes.
  useEffect(() => {
    if (!openPath) {
      setDoc(null);
      return;
    }
    setLoading(true);
    let cancelled = false;
    void readFile(workspaceId, openPath)
      .then((content) => {
        if (cancelled) return;
        setDoc({
          path: content.path,
          encoding: content.encoding,
          text: content.text,
          status: "clean",
          errorReason: null,
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const reason =
          err instanceof ApiError
            ? `${err.code}: ${err.reason}`
            : err instanceof Error
              ? err.message
              : String(err);
        setDoc({
          path: openPath,
          encoding: "utf-8",
          text: "",
          status: "error",
          errorReason: reason,
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, openPath]);

  const flushSave = useCallback(
    (state: DocState) => {
      setDoc((prev) => (prev && prev.path === state.path ? { ...prev, status: "saving" } : prev));
      void writeFile(workspaceId, state.path, {
        content: state.text,
        encoding: state.encoding,
      })
        .then(() => {
          setDoc((prev) =>
            prev && prev.path === state.path
              ? { ...prev, status: "saved", errorReason: null }
              : prev,
          );
        })
        .catch((err: unknown) => {
          const reason =
            err instanceof ApiError
              ? `${err.code}: ${err.reason}`
              : err instanceof Error
                ? err.message
                : String(err);
          setDoc((prev) =>
            prev && prev.path === state.path
              ? { ...prev, status: "error", errorReason: reason }
              : prev,
          );
        });
    },
    [workspaceId],
  );

  const onChange = useCallback(
    (value: string | undefined) => {
      const next = value ?? "";
      setDoc((prev) =>
        prev && prev.encoding === "utf-8"
          ? { ...prev, text: next, status: "dirty", errorReason: null }
          : prev,
      );
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => {
        setDoc((current) => {
          if (current && current.encoding === "utf-8") flushSave({ ...current, text: next });
          return current;
        });
      }, AUTOSAVE_DEBOUNCE_MS);
    },
    [flushSave],
  );

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  const language = useMemo(() => (doc ? languageFor(doc.path) : "plaintext"), [doc]);

  // No-op for now — Cycle 3.14 wires the dark theme through here.
  const onMount: OnMount = () => undefined;

  if (!openPath) {
    return <p className="ide-editor-empty">{t("ide.editor.empty")}</p>;
  }
  if (loading || !doc) {
    return <p className="ide-editor-loading">{t("ide.editor.loading")}</p>;
  }
  if (doc.encoding === "base64") {
    return (
      <div className="ide-editor-binary" data-testid="editor-binary">
        <p>{t("ide.editor.binary")}</p>
        <code>{doc.path}</code>
      </div>
    );
  }

  return (
    <div className="ide-editor">
      <header className="ide-editor-header" data-testid="editor-header">
        <span className="ide-editor-path">{doc.path}</span>
        <span
          className={`ide-editor-status ide-editor-status--${doc.status}`}
          data-testid="editor-status"
        >
          {doc.status === "dirty"
            ? t("ide.editor.dirty")
            : doc.status === "saving"
              ? t("ide.editor.saving")
              : doc.status === "saved"
                ? t("ide.editor.saved")
                : doc.status === "error" && doc.errorReason
                  ? doc.errorReason
                  : ""}
        </span>
      </header>
      <div className="ide-editor-body">
        <Editor
          height="100%"
          language={language}
          value={doc.text}
          onChange={onChange}
          onMount={onMount}
          options={{
            minimap: { enabled: true },
            scrollBeyondLastLine: false,
            tabSize: 2,
            automaticLayout: true,
            wordWrap: "on",
          }}
        />
      </div>
    </div>
  );
}
