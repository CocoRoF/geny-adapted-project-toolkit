import {
  forwardRef,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import { cn } from "@/ui/cn";

const baseField =
  "flex w-full rounded-md border border-border bg-surface px-2.5 py-1.5 text-[13px] text-fg shadow-none transition-colors placeholder:text-fg-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type = "text", ...props }, ref) => (
    <input ref={ref} type={type} className={cn(baseField, "h-8", className)} {...props} />
  ),
);
Input.displayName = "Input";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, rows = 4, ...props }, ref) => (
  <textarea
    ref={ref}
    rows={rows}
    className={cn(baseField, "min-h-[80px] py-2", className)}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select ref={ref} className={cn(baseField, "h-8 pr-7 appearance-none", className)} {...props}>
      {children}
    </select>
  ),
);
Select.displayName = "Select";

interface FieldProps {
  label: string;
  hint?: string | undefined;
  error?: string | null;
  children: React.ReactNode;
  /** When true, lay label out beside the input rather than above. */
  inline?: boolean;
}

/** Label + control + hint/error block. Wraps any `Input`/`Select`/etc. */
export function Field({ label, hint, error, children, inline = false }: FieldProps) {
  if (inline) {
    return (
      <label className="flex flex-row items-center gap-3">
        <span className="text-[12px] font-medium text-fg-muted w-28 shrink-0">{label}</span>
        <div className="flex-1">
          {children}
          {hint && !error ? <p className="mt-1 text-[11px] text-fg-subtle">{hint}</p> : null}
          {error ? <p className="mt-1 text-[11px] text-danger">{error}</p> : null}
        </div>
      </label>
    );
  }
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[12px] font-medium text-fg-muted">{label}</span>
      {children}
      {hint && !error ? <p className="text-[11px] text-fg-subtle">{hint}</p> : null}
      {error ? <p className="text-[11px] text-danger">{error}</p> : null}
    </label>
  );
}
