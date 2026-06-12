import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, FlaskConical, Loader2, Play, Square, XCircle } from "lucide-react";

import { getIntrospection } from "@/api/introspect";
import { streamTestRun, type TestRunFrame } from "@/api/tests";
import { Badge } from "@/ui/Badge";
import { Button } from "@/ui/Button";

interface Props {
  workspaceId: string;
}

interface RunState {
  command: string;
  cwd: string | null;
  exitCode: number | null;
  durationMs: number | null;
  lines: string[];
  running: boolean;
  startedAt: number;
}

/** One-click test runner. Streams stdout/stderr from inside the
 * workspace sandbox, shows the exit code + duration on terminal,
 * keeps a scroll-buffer of the last N lines (rolling so a 50k-line
 * pytest run doesn't murder the DOM). */
export function TestRunnerPanel({ workspaceId }: Props) {
  const [defaultCmd, setDefaultCmd] = useState<string | null>(null);
  const [defaultCwd, setDefaultCwd] = useState<string | null>(null);
  const [override, setOverride] = useState("");
  const [run, setRun] = useState<RunState | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    getIntrospection(workspaceId)
      .then((r) => {
        setDefaultCmd(r.test_command);
        setDefaultCwd(r.dev_cwd ?? null);
      })
      .catch(() => {
        // Detector unreachable — leave the override empty, user types one.
      });
  }, [workspaceId]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) {
      el.scrollTop = el.scrollHeight;
    }
  }, [run?.lines.length]);

  const start = useCallback(() => {
    abortRef.current?.abort();
    const cmd = (override || defaultCmd || "").trim();
    if (!cmd) return;
    setRun({
      command: cmd,
      cwd: defaultCwd,
      exitCode: null,
      durationMs: null,
      lines: [],
      running: true,
      startedAt: Date.now(),
    });
    const ctrl = streamTestRun(
      workspaceId,
      { command: override || null, cwd: defaultCwd },
      (frame: TestRunFrame) => {
        setRun((cur) => {
          if (!cur) return cur;
          if (frame.type === "log" && frame.line !== undefined) {
            // Roll the buffer so we never grow past 2000 lines.
            const next = [...cur.lines, `${frame.stream === "err" ? "✗ " : ""}${frame.line}`];
            if (next.length > 2000) next.splice(0, next.length - 2000);
            return { ...cur, lines: next };
          }
          if (frame.type === "done") {
            return {
              ...cur,
              running: false,
              exitCode: frame.exit_code ?? -1,
              durationMs: frame.duration_ms ?? null,
            };
          }
          return cur;
        });
      },
    );
    abortRef.current = ctrl;
  }, [defaultCmd, defaultCwd, override, workspaceId]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRun((cur) => (cur ? { ...cur, running: false } : cur));
  }, []);

  const effectiveCmd = override || defaultCmd || "";

  return (
    <div className="flex h-full flex-col bg-bg-elevated">
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <FlaskConical className="h-3.5 w-3.5 text-fg-muted" />
        <span className="text-[12px] font-semibold text-fg">테스트 러너</span>
        <input
          type="text"
          value={override}
          onChange={(e) => setOverride(e.currentTarget.value)}
          placeholder={defaultCmd ?? "예: pytest, npm test, vitest run -t auth"}
          className="ml-2 flex-1 rounded-md border border-border bg-bg px-2 py-1 font-mono text-[12px] text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
        />
        {run?.running ? (
          <Button variant="danger" onClick={cancel}>
            <Square className="mr-1 h-3 w-3" />
            중단
          </Button>
        ) : (
          <Button variant="primary" onClick={start} disabled={!effectiveCmd}>
            <Play className="mr-1 h-3 w-3" />
            실행
          </Button>
        )}
        {run?.exitCode !== null && run?.exitCode !== undefined ? (
          run.exitCode === 0 ? (
            <Badge tone="success">
              <CheckCircle2 className="mr-1 inline h-3 w-3" />
              exit 0
            </Badge>
          ) : (
            <Badge tone="danger">
              <XCircle className="mr-1 inline h-3 w-3" />
              exit {run.exitCode}
            </Badge>
          )
        ) : null}
        {run?.durationMs !== null && run?.durationMs !== undefined ? (
          <span className="text-[11px] text-fg-subtle">{(run.durationMs / 1000).toFixed(1)}s</span>
        ) : null}
      </header>
      {!run ? (
        <div className="flex flex-1 items-center justify-center text-[12px] text-fg-subtle">
          {defaultCmd ? (
            <>
              감지된 명령:{" "}
              <code className="ml-1 rounded bg-bg-elevated px-1 font-mono">{defaultCmd}</code>
              <span className="mx-1 text-fg-subtle">·</span>
              <span>실행을 누르세요</span>
            </>
          ) : (
            "테스트 명령을 입력하고 실행하세요."
          )}
        </div>
      ) : (
        <pre
          ref={logRef}
          className="flex-1 overflow-auto whitespace-pre-wrap break-all bg-bg px-4 py-3 font-mono text-[11px] leading-relaxed text-fg-muted"
        >
          {run.lines.length === 0 ? (
            <span className="flex items-center gap-1 text-fg-subtle">
              <Loader2 className="h-3 w-3 animate-spin" /> {run.command} 시작 중…
            </span>
          ) : (
            run.lines.join("\n")
          )}
        </pre>
      )}
    </div>
  );
}
