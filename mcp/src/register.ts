import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { z, ZodRawShape } from "zod";

import { GaptApiError } from "./client.js";

type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  isError?: boolean;
};

/** Register one tool with uniform output + error handling.
 *
 * - Handler returns any JSON-able value → pretty-printed text content.
 *   (Plain string passes through untouched — log tails, diffs.)
 * - GaptApiError → isError result with the structured "[status] code:
 *   reason" line, so the calling agent can react to specific codes
 *   (e.g. git.repo_not_cloned → rehydrate) instead of a stack trace.
 *
 * The cast at the `server.tool` boundary sidesteps the SDK's 6-way
 * overload matrix; OUR signature stays fully typed for callers (zod
 * shape in, inferred args out).
 */
export function tool<Shape extends ZodRawShape>(
  server: McpServer,
  name: string,
  description: string,
  shape: Shape,
  handler: (args: z.objectOutputType<Shape, z.ZodTypeAny>) => Promise<unknown>,
): void {
  const cb = async (args: z.objectOutputType<Shape, z.ZodTypeAny>): Promise<ToolResult> => {
    try {
      const out = await handler(args);
      const text =
        typeof out === "string" ? out : JSON.stringify(out ?? { ok: true }, null, 2);
      return { content: [{ type: "text", text }] };
    } catch (e) {
      if (e instanceof GaptApiError) {
        return { isError: true, content: [{ type: "text", text: e.message }] };
      }
      const msg = e instanceof Error ? e.message : String(e);
      return { isError: true, content: [{ type: "text", text: `gapt-mcp error: ${msg}` }] };
    }
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (server.tool as any)(name, description, shape, cb);
}
