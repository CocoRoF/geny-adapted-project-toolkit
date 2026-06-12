import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { cn } from "@/ui/cn";

interface Props {
  /** Free-text + selected value. Parent owns the string so the
   * surrounding form can submit it even when the dropdown is closed. */
  value: string;
  onChange: (next: string) => void;
  /** All known options. Empty array → behaves like a plain Input so
   * the form is still usable while options are loading or failed. */
  options: string[];
  /** Loading state — shows a hint inside the dropdown instead of
   * "no match". Doesn't disable the input. */
  loading?: boolean;
  /** Right-side affordance (a refresh button, info icon, etc.). */
  trailing?: ReactNode;
  placeholder?: string;
  required?: boolean;
  maxLength?: number;
  /** Hint shown under the input when nothing else is going on. */
  hint?: ReactNode;
  /** Localized "no match — press Enter to use anyway" hint. */
  noMatchHint?: string;
  /** Forwarded id so a parent <Field> label can target the input. */
  id?: string;
}

/** Single-line text input that drops a filterable list of suggestions
 * underneath. Unlike a pure `<select>`, the user can submit any
 * string — useful for things like "create a workspace on a branch
 * that doesn't exist on the remote yet". */
export function Combobox({
  value,
  onChange,
  options,
  loading = false,
  trailing,
  placeholder,
  required,
  maxLength,
  hint,
  noMatchHint,
  id: idProp,
}: Props) {
  const reactId = useId();
  const id = idProp ?? `combobox-${reactId}`;
  const listboxId = `${id}-listbox`;
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);

  // Filter case-insensitively by substring. Exact match floats to top
  // so "main" → "main" is the first item even with "remain" in the list.
  const filtered = (() => {
    const q = value.trim().toLowerCase();
    if (!q) return options;
    const sorted = options.filter((o) => o.toLowerCase().includes(q));
    sorted.sort((a, b) => {
      const ae = a.toLowerCase() === q ? 0 : a.toLowerCase().startsWith(q) ? 1 : 2;
      const be = b.toLowerCase() === q ? 0 : b.toLowerCase().startsWith(q) ? 1 : 2;
      return ae - be;
    });
    return sorted;
  })();

  // Reset highlight when the filtered set shifts so the chevron isn't
  // pointing at a removed item.
  useEffect(() => {
    setHighlight(0);
  }, [value, options]);

  // Outside-click closes the dropdown without committing — the input
  // already holds the latest typed value via onChange.
  useEffect(() => {
    if (!open) return;
    function onDown(ev: MouseEvent) {
      if (!containerRef.current?.contains(ev.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function commit(next: string) {
    onChange(next);
    setOpen(false);
    inputRef.current?.focus();
  }

  function onKeyDown(ev: KeyboardEvent<HTMLInputElement>) {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setHighlight((h) => Math.min(h + 1, Math.max(filtered.length - 1, 0)));
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (ev.key === "Enter") {
      // Enter on a highlighted suggestion picks it; otherwise lets the
      // form submit with whatever was typed. We don't preventDefault
      // for the "no highlight" case so submit semantics still work.
      const picked = filtered[highlight];
      if (open && picked !== undefined && picked !== value) {
        ev.preventDefault();
        commit(picked);
      } else {
        setOpen(false);
      }
    } else if (ev.key === "Escape") {
      if (open) {
        ev.preventDefault();
        setOpen(false);
      }
    }
  }

  const showNoMatch = open && !loading && filtered.length === 0 && value.length > 0 && noMatchHint;

  return (
    <div ref={containerRef} className="relative">
      <div className="flex items-stretch gap-1">
        <input
          ref={inputRef}
          id={id}
          type="text"
          value={value}
          onChange={(e) => {
            onChange(e.currentTarget.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          required={required}
          maxLength={maxLength}
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          className="flex w-full rounded-md border border-border bg-surface px-2.5 py-1.5 h-8 text-[13px] text-fg shadow-none transition-colors placeholder:text-fg-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-50"
        />
        {trailing ? <div className="flex shrink-0 items-center">{trailing}</div> : null}
      </div>

      {open && (loading || filtered.length > 0 || showNoMatch) ? (
        <ul
          id={listboxId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-md border border-border bg-surface py-1 text-[13px] shadow-lg"
        >
          {loading ? (
            <li className="px-2.5 py-1.5 text-fg-subtle">…</li>
          ) : (
            filtered.map((opt, i) => (
              <li
                key={opt}
                role="option"
                aria-selected={opt === value}
                onMouseDown={(e) => {
                  // mousedown (not click) so the input's blur doesn't
                  // close the popup before the click registers.
                  e.preventDefault();
                  commit(opt);
                }}
                onMouseEnter={() => setHighlight(i)}
                className={cn(
                  "cursor-pointer px-2.5 py-1.5 text-fg",
                  i === highlight ? "bg-accent/10" : null,
                  opt === value ? "font-medium" : null,
                )}
              >
                {opt}
              </li>
            ))
          )}
          {showNoMatch ? (
            <li className="px-2.5 py-1.5 text-fg-subtle italic">{noMatchHint}</li>
          ) : null}
        </ul>
      ) : null}

      {hint ? <p className="mt-1 text-[11px] text-fg-subtle">{hint}</p> : null}
    </div>
  );
}
