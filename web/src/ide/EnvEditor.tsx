import { useCallback, useEffect, useMemo, useState } from "react";
import { Copy, FileText, Loader2, RotateCcw, Save, Sparkles } from "lucide-react";

import { type FileContent, readFile, writeFile } from "@/api/files";
import { getIntrospection, type IntrospectResponse } from "@/api/introspect";
import { Button } from "@/ui/Button";

interface Props {
  workspaceId: string;
}

type LoadState = "idle" | "loading" | "ready" | "error";

/** .env editor — surfaces every env file the introspector found
 * plus their `.example`/`.template` siblings. Reads/writes go
 * through the workspace files API, which routes through the
 * sandbox so the agent runtime and the editor see the same bytes.
 *
 * Workflow:
 *   1. On mount: ask `/introspect` for `env_files` + `env_examples`.
 *   2. User picks a file from the left list.
 *   3. Right pane shows its contents in a plain textarea.
 *   4. "Seed from example" pulls the .example content for missing
 *      env files (one-click bootstrap).
 *   5. Save writes back via PUT /file.
 *
 * Why a dedicated panel instead of editing through the file tree:
 * .env files are usually scattered (`backend/.env`, `frontend/.env`,
 * root `.env`) and finding them in a deep tree slows users down.
 * This pane is one screen, one list, one save. */
export function EnvEditor({ workspaceId }: Props) {
  const [intro, setIntro] = useState<IntrospectResponse | null>(null);
  const [introState, setIntroState] = useState<LoadState>("loading");
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [originalContent, setOriginalContent] = useState<string>("");
  const [contentState, setContentState] = useState<LoadState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Refresh the introspection on mount so the list reflects any
  // files added since the workspace was first opened.
  useEffect(() => {
    setIntroState("loading");
    getIntrospection(workspaceId)
      .then((r) => {
        setIntro(r);
        setIntroState("ready");
        // Auto-select the first env file (or first example if no
        // env exists yet — user can then seed it).
        const initial = r.env_files[0] ?? r.env_examples[0] ?? null;
        if (initial) setSelected(initial);
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e));
        setIntroState("error");
      });
  }, [workspaceId]);

  useEffect(() => {
    if (!selected) return;
    setContentState("loading");
    setError(null);
    readFile(workspaceId, selected)
      .then((f: FileContent) => {
        setContent(f.text);
        setOriginalContent(f.text);
        setContentState("ready");
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e));
        setContentState("error");
      });
  }, [workspaceId, selected]);

  const dirty = useMemo(() => content !== originalContent, [content, originalContent]);

  const seedExampleFor = useCallback(
    (filePath: string) => {
      if (!intro) return;
      // Map env file → matching example. Heuristic: same dir + same
      // stem before `.env` part. `backend/.env` → `backend/.env.example`.
      const dir = filePath.includes("/")
        ? filePath.slice(0, filePath.lastIndexOf("/"))
        : "";
      const exampleCandidates = [
        `${dir}/.env.example`.replace(/^\/+/, ""),
        `${dir}/.env.template`.replace(/^\/+/, ""),
        `${dir}/.env.sample`.replace(/^\/+/, ""),
      ];
      const match = intro.env_examples.find((p) => exampleCandidates.includes(p));
      if (!match) {
        setError("이 위치에 짝지을 .env.example 파일이 없습니다.");
        return;
      }
      readFile(workspaceId, match)
        .then((f) => setContent(f.text))
        .catch((e) =>
          setError(e instanceof Error ? e.message : String(e)),
        );
    },
    [intro, workspaceId],
  );

  const handleSave = useCallback(async () => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      await writeFile(workspaceId, selected, { content });
      setOriginalContent(content);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [content, selected, workspaceId]);

  // Tracked alongside `env_files` because users often want to
  // bootstrap a missing `.env` from its example with one click —
  // we surface examples as "editable" too with a hint.
  const allFiles = useMemo(() => {
    if (!intro) return [] as Array<{ path: string; isExample: boolean }>;
    const seen = new Set<string>();
    const out: Array<{ path: string; isExample: boolean }> = [];
    for (const p of intro.env_files) {
      if (!seen.has(p)) {
        seen.add(p);
        out.push({ path: p, isExample: false });
      }
    }
    for (const p of intro.env_examples) {
      if (!seen.has(p)) {
        seen.add(p);
        out.push({ path: p, isExample: true });
      }
    }
    return out;
  }, [intro]);

  return (
    <div className="grid h-full grid-cols-[minmax(220px,280px)_1fr]">
      <aside className="flex h-full flex-col overflow-hidden border-r border-border bg-bg-elevated">
        <header className="flex shrink-0 items-center gap-1.5 border-b border-border px-3 py-2 text-[12px] font-semibold text-fg">
          <FileText className="h-3.5 w-3.5 text-fg-muted" />
          .env 파일
        </header>
        <div className="flex-1 overflow-y-auto py-1">
          {introState === "loading" ? (
            <p className="px-3 py-2 text-[11px] text-fg-subtle">불러오는 중…</p>
          ) : allFiles.length === 0 ? (
            <p className="px-3 py-3 text-[11px] text-fg-subtle">
              감지된 .env 파일이 없습니다. 워크스페이스 파일 트리에서 직접 추가하세요.
            </p>
          ) : (
            <ul className="space-y-0.5 px-1.5">
              {allFiles.map(({ path, isExample }) => (
                <li key={path}>
                  <button
                    type="button"
                    onClick={() => setSelected(path)}
                    className={
                      selected === path
                        ? "flex w-full items-center gap-1.5 rounded bg-accent/15 px-2 py-1 text-left text-[12px] font-medium text-fg"
                        : "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[12px] text-fg-muted hover:bg-surface-hover hover:text-fg"
                    }
                  >
                    <span className="truncate font-mono">{path}</span>
                    {isExample ? (
                      <span className="ml-auto shrink-0 rounded bg-bg px-1 text-[10px] text-fg-subtle">
                        example
                      </span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
      <main className="flex h-full flex-col overflow-hidden bg-bg">
        <header className="flex shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-3 py-2">
          <span className="font-mono text-[12px] text-fg">
            {selected ?? "파일을 선택하세요"}
          </span>
          {dirty ? (
            <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] font-medium text-warn">
              미저장
            </span>
          ) : null}
          <div className="ml-auto flex items-center gap-1">
            {selected && !selected.endsWith(".example") ? (
              <Button
                variant="secondary"
                onClick={() => seedExampleFor(selected)}
                disabled={contentState !== "ready"}
                title=".env.example에서 채워넣기"
              >
                <Sparkles className="mr-1 h-3 w-3" />
                예제에서 채우기
              </Button>
            ) : null}
            <Button
              variant="secondary"
              onClick={() => setContent(originalContent)}
              disabled={!dirty}
            >
              <RotateCcw className="mr-1 h-3 w-3" />
              되돌리기
            </Button>
            <Button
              variant="primary"
              onClick={handleSave}
              disabled={!dirty || saving || !selected}
            >
              {saving ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <Save className="mr-1 h-3 w-3" />
              )}
              저장
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                if (navigator.clipboard) void navigator.clipboard.writeText(content);
              }}
              disabled={!selected}
              title="복사"
            >
              <Copy className="h-3 w-3" />
            </Button>
          </div>
        </header>
        {error ? (
          <p className="border-b border-danger/40 bg-danger/10 px-3 py-1.5 text-[11px] text-danger">
            {error}
          </p>
        ) : null}
        {contentState === "loading" ? (
          <div className="flex flex-1 items-center justify-center text-[12px] text-fg-subtle">
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            불러오는 중…
          </div>
        ) : selected ? (
          <textarea
            value={content}
            onChange={(e) => setContent(e.currentTarget.value)}
            spellCheck={false}
            className="flex-1 resize-none overflow-auto bg-bg px-4 py-3 font-mono text-[12px] leading-relaxed text-fg outline-none"
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-[12px] text-fg-subtle">
            좌측에서 .env 파일을 선택하세요.
          </div>
        )}
      </main>
    </div>
  );
}
