import { useEffect, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { useTheme } from "@/app/providers/theme-context";
import { asString, parseJsonObject } from "@/lib/json";

interface Props {
  workspaceId: string;
}

type ConnState = "connecting" | "open" | "closed" | "error";

/** Interactive shell over WebSocket → server PTY. One PTY per
 * connection; the panel auto-reconnects on transient drops but
 * preserves the last on-screen buffer so the user keeps context.
 *
 * Protocol matches `routers/terminal.py`:
 *   client → server: {type:"input", data: string}
 *                    {type:"resize", rows, cols}
 *                    {type:"ping"}
 *   server → client: {type:"output", data: string}
 *                    {type:"exit", code: number}
 *                    {type:"error", code, reason}
 *                    {type:"pong"}
 */
export function TerminalPanel({ workspaceId }: Props) {
  const { resolved: themeResolved } = useTheme();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [state, setState] = useState<ConnState>("connecting");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Mount terminal once.
  useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace",
      fontSize: 13,
      scrollback: 5000,
      allowProposedApi: true,
      theme: terminalTheme(themeResolved),
    });
    const fit = new FitAddon();
    const links = new WebLinksAddon();
    term.loadAddon(fit);
    term.loadAddon(links);
    term.open(containerRef.current);
    try {
      fit.fit();
    } catch {
      // happens on hidden mount; the size observer below retries.
    }
    termRef.current = term;
    fitRef.current = fit;

    const observer = new ResizeObserver(() => {
      try {
        fit.fit();
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
        }
      } catch {
        // ignore — terminal element may not be in layout yet
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [themeResolved]);

  // Connect / reconnect.
  useEffect(() => {
    let cancelled = false;
    let retryHandle: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      const term = termRef.current;
      if (!term) return;

      // Build the URL. EventSource doesn't work for WS so we go through
      // the same proxied origin. `ws(s):` scheme matches the page.
      //
      // FIX: every other GAPT route is at `/_gapt/api/...`; the
      // terminal endpoint also mounts at `prefix="/_gapt/api/workspaces"`.
      // Pre-fix the URL was missing the `_gapt` prefix, so every
      // WebSocket upgrade got a 404 and the panel sat in "Connection
      // closed. Reconnecting…" forever.
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${scheme}//${location.host}/_gapt/api/workspaces/${workspaceId}/terminal?rows=${term.rows}&cols=${term.cols}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;
      setState("connecting");

      ws.onopen = () => {
        if (cancelled) {
          ws.close();
          return;
        }
        setState("open");
        setErrorMsg(null);
        // Send an initial resize so the server PTY matches what xterm
        // shows. The query string above was best-effort; this is
        // authoritative.
        ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
      };

      ws.onmessage = (event) => {
        try {
          const frame = parseJsonObject(event.data);
          if (!frame) return; // malformed frame — drop it
          const ftype = asString(frame["type"]);
          if (ftype === "output") {
            term.write(asString(frame["data"]));
          } else if (ftype === "exit") {
            const code = Number(frame["code"] ?? 0);
            term.write(`\r\n\x1b[2m── shell exited with code ${code} ──\x1b[0m\r\n`);
          } else if (ftype === "error") {
            const code = asString(frame["code"], "error");
            const reason = asString(frame["reason"]);
            term.write(`\r\n\x1b[31m${code}: ${reason}\x1b[0m\r\n`);
          }
        } catch {
          // Malformed frame — drop it.
        }
      };

      ws.onerror = () => {
        if (cancelled) return;
        setState("error");
        setErrorMsg("Connection error.");
      };

      ws.onclose = (event) => {
        if (cancelled) return;
        setState("closed");
        // App-close codes (4xxx) are intentional terminal states —
        // auth, not-found, etc. Don't auto-reconnect on those.
        if (event.code >= 4000 && event.code < 5000) {
          setErrorMsg(event.reason || `closed (${event.code})`);
          return;
        }
        // Transport-level closes get one reconnect attempt after a
        // short backoff so the panel survives a server restart.
        setErrorMsg("Connection closed. Reconnecting…");
        retryHandle = setTimeout(() => {
          if (!cancelled) connect();
        }, 1500);
      };

      term.onData((data) => {
        if (ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({ type: "input", data }));
      });
    }

    // Defer one tick so the xterm element exists in the DOM tree.
    const launch = setTimeout(connect, 0);

    return () => {
      cancelled = true;
      clearTimeout(launch);
      if (retryHandle) clearTimeout(retryHandle);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [workspaceId]);

  return (
    <div className="flex h-full flex-col bg-bg">
      <header
        data-testid="terminal-header"
        className="flex h-7 shrink-0 items-center gap-2 border-b border-border bg-bg-elevated px-3 text-[11px]"
      >
        <span
          className={
            state === "open"
              ? "h-1.5 w-1.5 rounded-full bg-success"
              : state === "error"
                ? "h-1.5 w-1.5 rounded-full bg-danger"
                : state === "closed"
                  ? "h-1.5 w-1.5 rounded-full bg-fg-subtle"
                  : "h-1.5 w-1.5 animate-pulse rounded-full bg-accent"
          }
        />
        <span className="text-fg-muted">
          {state === "open"
            ? "connected"
            : state === "connecting"
              ? "connecting…"
              : state === "closed"
                ? "closed"
                : "error"}
        </span>
        {errorMsg ? (
          <span className="ml-auto truncate text-danger" title={errorMsg}>
            {errorMsg}
          </span>
        ) : null}
      </header>
      <div
        ref={containerRef}
        data-testid="terminal-canvas"
        className="flex-1 overflow-hidden bg-bg"
        style={{ padding: "4px 6px" }}
      />
    </div>
  );
}

function terminalTheme(resolved: "light" | "dark") {
  if (resolved === "dark") {
    return {
      background: "#0b0d10",
      foreground: "#e6e6e6",
      cursor: "#7aa2f7",
      black: "#15161e",
      brightBlack: "#414868",
      red: "#f7768e",
      brightRed: "#ff7a93",
      green: "#9ece6a",
      brightGreen: "#b9f27c",
      yellow: "#e0af68",
      brightYellow: "#ffc777",
      blue: "#7aa2f7",
      brightBlue: "#8eb6f5",
      magenta: "#bb9af7",
      brightMagenta: "#c8b6ff",
      cyan: "#7dcfff",
      brightCyan: "#a4daff",
      white: "#a9b1d6",
      brightWhite: "#f0f0f0",
      selectionBackground: "#33467c",
    };
  }
  return {
    background: "#ffffff",
    foreground: "#1f2328",
    cursor: "#0969da",
    black: "#24292f",
    red: "#cf222e",
    green: "#1a7f37",
    yellow: "#9a6700",
    blue: "#0969da",
    magenta: "#8250df",
    cyan: "#1b7c83",
    white: "#6e7781",
    brightBlack: "#57606a",
    brightRed: "#a40e26",
    brightGreen: "#116329",
    brightYellow: "#7d4e00",
    brightBlue: "#0550ae",
    brightMagenta: "#6639ba",
    brightCyan: "#1b7c83",
    brightWhite: "#8c959f",
    selectionBackground: "#0969da33",
  };
}
