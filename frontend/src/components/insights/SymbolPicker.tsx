import { Search, X } from "lucide-react";
import { useDeferredValue, useEffect, useId, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { SymbolHit } from "@/lib/api";
import { useSymbols } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.insights.impact;

/**
 * A searchable combobox for functions, methods and classes. Type to search; the arrow keys,
 * Enter and Escape work as in a native select. Results come from the server, best match first.
 */
export function SymbolPicker({
  repoId,
  value,
  onChange,
}: {
  repoId: string;
  value: SymbolHit | null;
  onChange: (symbol: SymbolHit | null) => void;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const query = useDeferredValue(text);
  const { data } = useSymbols(repoId, query);
  const root = useRef<HTMLDivElement>(null);
  const listId = useId();
  const options = data?.symbols ?? [];

  // Close when the user clicks elsewhere.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const choose = (symbol: SymbolHit) => {
    onChange(symbol);
    setText("");
    setOpen(false);
  };

  if (value) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border bg-background py-1.5 pr-1.5 pl-3">
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium">
          {value.qualified_name}
        </span>
        <Badge>{value.kind}</Badge>
        <span className="hidden truncate font-mono text-xs text-muted sm:inline">
          {value.file_path}
        </span>
        <Button variant="subtle" size="icon-sm" aria-label={s.clear} onClick={() => onChange(null)}>
          <X />
        </Button>
      </div>
    );
  }

  return (
    <div ref={root} className="relative">
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
        <input
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
          aria-autocomplete="list"
          aria-label={s.pick}
          value={text}
          placeholder={s.pick}
          spellCheck={false}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setActive((i) => Math.min(i + 1, options.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter" && open && options[active]) {
              e.preventDefault();
              choose(options[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          className="h-10 w-full rounded-md border border-border bg-background pr-3 pl-9 font-mono text-[13px] text-foreground placeholder:font-sans placeholder:text-muted focus-visible:border-border-strong focus-visible:ring-4 focus-visible:ring-foreground/10 focus-visible:outline-none"
        />
      </div>
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={s.pick}
          className="absolute z-20 mt-1 max-h-80 w-full overflow-y-auto rounded-md border border-border bg-background p-1 shadow-sm"
        >
          {options.length === 0 ? (
            <li className="px-3 py-3 text-[13px] text-muted">{s.pickHint}</li>
          ) : (
            options.map((symbol, i) => (
              <li
                key={symbol.id}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault(); // keep focus in the input
                  choose(symbol);
                }}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-sm px-2.5 py-2",
                  i === active && "bg-surface-2",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-[13px]">
                    {symbol.qualified_name}
                  </span>
                  <span className="block truncate font-mono text-[11px] text-muted">
                    {symbol.file_path}:{symbol.line}
                  </span>
                </span>
                <Badge>{symbol.kind}</Badge>
                {symbol.is_test && <Badge>{s.isTest}</Badge>}
                <span className="w-16 shrink-0 text-right text-xs text-muted tabular-nums">
                  {s.callers(symbol.callers)}
                </span>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
