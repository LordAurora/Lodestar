import { ChevronRight, FileCode2 } from "lucide-react";
import { useState } from "react";
import type { Source } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { cn } from "@/lib/utils";

export function SourceRow({ source, n, note }: { source: Source; n?: number; note?: string }) {
  const { openSource, source: shown } = useAppState();
  const active = shown?.kind === "chunk" && shown.id === source.chunk_id;
  return (
    <li>
      <button
        onClick={() => openSource(source.chunk_id)}
        className={cn(
          "flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left hover:bg-surface-2",
          active && "bg-surface-2",
        )}
      >
        {n !== undefined ? (
          <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[11px] font-medium text-accent-text">
            {n}
          </span>
        ) : (
          <FileCode2 className="size-4 shrink-0 text-muted" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[12.5px]">
            {source.file_path}
            <span className="text-muted">
              :{source.start_line}-{source.end_line}
            </span>
          </span>
          {source.symbol_name && (
            <span className="block truncate text-xs text-muted">
              {source.symbol_kind} · {source.symbol_name}
            </span>
          )}
        </span>
        <span className="shrink-0 text-xs text-muted tabular-nums">
          {note ?? source.cosine.toFixed(2)}
        </span>
      </button>
    </li>
  );
}

/** Collapsible "Sources (n)" list under an answer. */
export function SourcesList({
  title,
  sources,
  numbers,
}: {
  title: string;
  sources: Source[];
  numbers: number[];
}) {
  const [open, setOpen] = useState(false);
  if (!sources.length) return null;
  return (
    <div className="rounded-md border border-border">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-[13px] font-medium text-muted hover:text-foreground"
      >
        <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
        {title}
      </button>
      {open && (
        <ul className="border-t border-border p-1">
          {sources.map((s, i) => (
            <SourceRow key={s.chunk_id} source={s} n={numbers[i]} />
          ))}
        </ul>
      )}
    </div>
  );
}
