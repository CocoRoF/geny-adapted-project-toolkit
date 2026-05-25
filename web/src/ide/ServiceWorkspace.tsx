import { useState } from "react";

import { PreviewPanel } from "@/ide/PreviewPanel";
import { ServicesPanel } from "@/ide/ServicesPanel";

interface Props {
  workspaceId: string;
}

/** "Service" tab for a workspace — a simple two-pane view: the
 * services list/controller on the left, the preview iframe on the
 * right. Intentionally NOT a dockview: the user pushed back on
 * preset-switching as friction, and this view's job is just "tell me
 * what's running and let me look at it."
 *
 * The `previewNonce` is bumped whenever the user does something in
 * ServicesPanel that should make the preview re-load (Expose,
 * Restart, Unexpose). PreviewPanel watches the nonce and bumps its
 * iframe `key` — so the iframe alone refreshes, NOT the whole tab.
 * Without this hook, clicking Expose only refreshed the services
 * list, and the user had to manually hit the preview's Refresh
 * button to see the new upstream. */
export function ServiceWorkspace({ workspaceId }: Props) {
  const [previewNonce, setPreviewNonce] = useState(0);
  return (
    <div className="grid h-full flex-1 grid-cols-[minmax(360px,_440px)_1fr] overflow-hidden">
      <aside className="overflow-y-auto border-r border-border bg-bg-elevated">
        <ServicesPanel
          workspaceId={workspaceId}
          onServicesMutated={() => setPreviewNonce((n) => n + 1)}
        />
      </aside>
      <main className="overflow-hidden bg-bg-elevated">
        <PreviewPanel workspaceId={workspaceId} reloadNonce={previewNonce} />
      </main>
    </div>
  );
}
