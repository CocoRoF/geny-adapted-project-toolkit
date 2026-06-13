import { useCallback, useEffect, useMemo, useState } from "react";
import { FileText, Loader2, Sparkles } from "lucide-react";

import { readFile, writeFile } from "@/api/files";
import { getIntrospection, type IntrospectResponse } from "@/api/introspect";
import { useI18n } from "@/app/providers/i18n-context";

interface Props {
  workspaceId: string;
  /** Push a file path into the shell's main editor column — same
   * callback the file tree uses. Clicking an env row reuses the
   * editor's Monaco for view/edit/save, so this panel stays a
   * pure picker. */
  onOpenFile: (path: string) => void;
}

type LoadState = "idle" | "loading" | "ready" | "error";

/** `.env` files picker.
 *
 * The introspector walks the workspace for `*.env` / `*.env.example`
 * etc. files; this panel renders them as a focused list (separate
 * surface from the main file tree because env files are typically
 * scattered and finding them in a deep tree slows the user down).
 *
 * Clicking a row routes the file into the main editor column via
 * `onOpenFile` so view/edit/save reuse the same Monaco surface as
 * every other file — no second editor to learn, no duplicate save
 * flow. The "예제에서 채우기" button on each row writes the matching
 * `.env.example` content into the row's `.env` path (one-click
 * bootstrap), then opens the freshly-seeded file. */
export function EnvEditor({ workspaceId, onOpenFile }: Props) {
  const { t } = useI18n();
  const [intro, setIntro] = useState<IntrospectResponse | null>(null);
  const [introState, setIntroState] = useState<LoadState>("loading");
  const [seedingPath, setSeedingPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setIntroState("loading");
    getIntrospection(workspaceId)
      .then((r) => {
        setIntro(r);
        setIntroState("ready");
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e));
        setIntroState("error");
      });
  }, [workspaceId]);

  const rows = useMemo(() => {
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

  /** Find the matching `.env.example` for an env file path, if any.
   * Looks for siblings in the same dir under the conventional
   * suffixes. Returns null when nothing matches. */
  const exampleFor = useCallback(
    (envPath: string): string | null => {
      if (!intro) return null;
      const dir = envPath.includes("/") ? envPath.slice(0, envPath.lastIndexOf("/")) : "";
      const candidates = [
        `${dir}/.env.example`.replace(/^\/+/, ""),
        `${dir}/.env.template`.replace(/^\/+/, ""),
        `${dir}/.env.sample`.replace(/^\/+/, ""),
      ];
      return intro.env_examples.find((p) => candidates.includes(p)) ?? null;
    },
    [intro],
  );

  const handleSeed = useCallback(
    async (envPath: string) => {
      const example = exampleFor(envPath);
      if (!example) {
        setError(t("env_editor.no_example_to_pair"));
        return;
      }
      setError(null);
      setSeedingPath(envPath);
      try {
        const content = await readFile(workspaceId, example);
        await writeFile(workspaceId, envPath, { content: content.text });
        // Open the freshly-seeded file so the operator can review +
        // tune values immediately. Same editor surface as every
        // other file click.
        onOpenFile(envPath);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setSeedingPath(null);
      }
    },
    [exampleFor, onOpenFile, t, workspaceId],
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex shrink-0 items-center gap-1.5 border-b border-border px-3 py-2 text-[11.5px] text-fg-muted">
        <FileText className="h-3.5 w-3.5" />
        <span>{t("env_editor.header")}</span>
      </header>
      {error ? (
        <p className="shrink-0 border-b border-danger/40 bg-danger/10 px-3 py-1.5 text-[11px] text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex-1 overflow-y-auto py-1">
        {introState === "loading" ? (
          <p className="px-3 py-2 text-[11px] text-fg-subtle">{t("env_editor.loading")}</p>
        ) : rows.length === 0 ? (
          <p className="px-3 py-3 text-[11px] text-fg-subtle">{t("env_editor.empty")}</p>
        ) : (
          <ul className="space-y-0.5 px-1.5">
            {rows.map(({ path, isExample }) => {
              const example = !isExample ? exampleFor(path) : null;
              const seeding = seedingPath === path;
              return (
                <li
                  key={path}
                  className="group flex items-center gap-1 rounded hover:bg-surface-hover"
                >
                  <button
                    type="button"
                    onClick={() => onOpenFile(path)}
                    className="flex min-w-0 flex-1 items-center gap-1.5 rounded px-2 py-1 text-left text-[12px] text-fg-muted hover:text-fg"
                    title={t("env_editor.open_in_editor").replace("{path}", path)}
                  >
                    <FileText className="h-3 w-3 shrink-0 text-fg-subtle" strokeWidth={1.5} />
                    <span className="truncate font-mono">{path}</span>
                    {isExample ? (
                      <span className="ml-auto shrink-0 rounded bg-bg px-1 text-[10px] text-fg-subtle">
                        example
                      </span>
                    ) : null}
                  </button>
                  {example ? (
                    <button
                      type="button"
                      onClick={() => void handleSeed(path)}
                      disabled={seeding}
                      title={t("env_editor.seed_from_example")
                        .replace("{example}", example)
                        .replace("{path}", path)}
                      className="mr-1 grid h-6 w-6 shrink-0 place-items-center rounded text-fg-subtle opacity-0 transition-opacity hover:bg-bg hover:text-fg group-hover:opacity-100 disabled:opacity-50"
                    >
                      {seeding ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Sparkles className="h-3 w-3" />
                      )}
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
