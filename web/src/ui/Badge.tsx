import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/ui/cn";

const badge = cva(
  // `whitespace-nowrap` keeps the badge contents on a single line so a
  // narrow parent (e.g. ToolCallCard header in a slim chat panel)
  // doesn't break "성공" into two stacked chars. Combine with
  // `shrink-0` at the call site to also prevent the badge itself from
  // being compressed.
  "inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium",
  {
    variants: {
      tone: {
        neutral: "border-border bg-bg-subtle text-fg-muted",
        accent: "border-accent/40 bg-accent/10 text-accent",
        success: "border-success/40 bg-success/10 text-success",
        warn: "border-warn/40 bg-warn/10 text-warn",
        danger: "border-danger/40 bg-danger/10 text-danger",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badge> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badge({ tone }), className)} {...props} />;
}
