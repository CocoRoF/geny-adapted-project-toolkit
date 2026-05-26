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
 * The two panels are loosely coupled: ServicesPanel polls the
 * services API, PreviewPanel polls it too. When a user clicks
 * Expose, the polling cycle picks up the new `bound_url` within a
 * few seconds and PreviewPanel's auto-select effect points the
 * iframe at it. We deliberately avoid pushing a "reload now" signal
 * from sibling to sibling — the iframe is large, re-mounting it on
 * every action made the whole tab look like it refreshed. */
export function ServiceWorkspace({ workspaceId }: Props) {
  return (
    <div className="grid h-full flex-1 grid-cols-[minmax(360px,_440px)_1fr] overflow-hidden">
      <aside className="overflow-y-auto border-r border-border bg-bg-elevated">
        <ServicesPanel workspaceId={workspaceId} />
      </aside>
      <main className="overflow-hidden bg-bg-elevated">
        <PreviewPanel workspaceId={workspaceId} />
      </main>
    </div>
  );
}
