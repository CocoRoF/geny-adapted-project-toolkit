import { createContext, useContext } from "react";

/** Tiny global pub/sub for "open this file in the editor".
 *
 * The dockview tree panel can't reach the editor panel via React
 * props (they live under separate dockview-managed roots), so we
 * thread an event emitter through context. `<DockviewShell>` owns
 * the emitter; `FileTreePanel` calls `emit(path)`; `EditorPanel`
 * subscribes. Replaced by router state once routing for individual
 * file URLs lands. */

type Subscriber = (path: string) => void;

export class EditorBus {
  private readonly subs = new Set<Subscriber>();

  emit(path: string): void {
    for (const sub of this.subs) sub(path);
  }

  subscribe(sub: Subscriber): () => void {
    this.subs.add(sub);
    return () => {
      this.subs.delete(sub);
    };
  }
}

export const EditorBusContext = createContext<EditorBus | null>(null);

export function useEditorBus(): EditorBus {
  const bus = useContext(EditorBusContext);
  if (bus === null) {
    throw new Error("useEditorBus must be used within an <EditorBusContext.Provider>");
  }
  return bus;
}
