import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";

import { getWorkspace } from "@/api/workspaces";
import { ChatPanel } from "@/chat/ChatPanel";

/** Standalone chat window — the devtools-undock counterpart of the
 * IDE's right-hand chat column. Mounted at
 * `/projects/:pid/w/:wid/chat` and opened via `window.open` from the
 * panel's pop-out button (a deep link works just as well: same
 * session cookie, same SSE stream as the docked panel).
 *
 * Deliberately chrome-less: no AppShell nav, no activity bar — the
 * ChatPanel IS the window. `?session=<id>` (appended by the pop-out
 * button) attaches it to the session the user was viewing. */
export function WorkspaceChatPopup() {
  const { pid, wid } = useParams<{ pid: string; wid: string }>();
  const [wsName, setWsName] = useState<string | null>(null);

  useEffect(() => {
    if (!wid) return;
    let cancelled = false;
    void getWorkspace(wid)
      .then((w) => {
        if (!cancelled) setWsName(w.name);
      })
      .catch(() => {
        // Title fallback below still works.
      });
    return () => {
      cancelled = true;
    };
  }, [wid]);

  useEffect(() => {
    const prev = document.title;
    document.title = `GAPT Chat — ${wsName ?? (wid ?? "")}`;
    return () => {
      document.title = prev;
    };
  }, [wid, wsName]);

  if (!pid || !wid) return <Navigate to="/projects" replace />;

  return (
    <div className="h-screen w-screen overflow-hidden bg-bg">
      <ChatPanel projectId={pid} workspaceId={wid} standalone />
    </div>
  );
}
