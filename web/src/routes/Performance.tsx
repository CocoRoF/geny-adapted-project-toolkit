import { PerformanceDashboard } from "@/performance/PerformanceDashboard";

/** `/performance` route — fleet-wide container resource dashboard.
 * Sister route to `/cost`. */
export function Performance() {
  return <PerformanceDashboard />;
}
