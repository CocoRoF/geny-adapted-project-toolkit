import { CostPanel } from "@/cost/CostPanel";

/** Standalone cost page — same panel as the dockview leaf at the
 * `/cost` route so the user can land on it without first opening a
 * workspace. */
export function Cost() {
  return (
    <div className="mx-auto max-w-[1080px]">
      <CostPanel />
    </div>
  );
}
