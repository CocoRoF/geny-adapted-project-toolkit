import { CostPanel } from "@/cost/CostPanel";

/** Standalone cost page — same panel as the dockview leaf, just at a
 * dedicated route so the user can land on it without first opening a
 * workspace. */
export function Cost() {
  return (
    <div className="route route-cost">
      <CostPanel />
    </div>
  );
}
