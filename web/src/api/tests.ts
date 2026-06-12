/** Test runner — SSE-streamed `<test_command>` output. The route
 * is POST so the body can carry overrides (run subset, cwd). */

export interface TestRunFrame {
  type: "meta" | "log" | "done";
  command?: string;
  cwd?: string;
  stream?: "out" | "err";
  line?: string;
  exit_code?: number;
  duration_ms?: number;
}

export interface RunTestsInput {
  command?: string | null;
  cwd?: string | null;
}

export function streamTestRun(
  workspaceId: string,
  body: RunTestsInput,
  onFrame: (frame: TestRunFrame) => void,
): AbortController {
  const ctrl = new AbortController();
  void (async () => {
    try {
      const resp = await fetch(`/_gapt/api/workspaces/${workspaceId}/tests/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
        credentials: "include",
      });
      if (!resp.ok || !resp.body) {
        onFrame({ type: "done", exit_code: -1, line: `HTTP ${resp.status}` });
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of chunk.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const raw = line.slice(5).trim();
            if (!raw) continue;
            try {
              onFrame(JSON.parse(raw) as TestRunFrame);
            } catch {
              // Malformed frame — skip silently.
            }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      onFrame({
        type: "done",
        exit_code: -1,
        line: String(err),
      });
    }
  })();
  return ctrl;
}
