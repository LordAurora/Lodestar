import { AlertTriangle, Copy, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import { useAppState } from "@/lib/app-state";
import { highlightLines } from "@/lib/highlight";
import { useChunk } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.source;

/** Right-hand drawer: the cited code with its line range highlighted. */
export function SourceDrawer() {
  const { sourceId, openSource } = useAppState();
  const { data, isLoading, error } = useChunk(sourceId);
  const [highlighted, setHighlighted] = useState<{ for: unknown; lines: string[] | null }>();
  const html = highlighted && highlighted.for === data ? highlighted.lines : null;
  const firstMarked = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!data) return;
    let cancelled = false;
    highlightLines(data.lines, data.language)
      .then((lines) => !cancelled && setHighlighted({ for: data, lines }))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [data]);

  useEffect(() => {
    firstMarked.current?.scrollIntoView({ block: "center" });
  }, [data, html]);

  const chunk = data?.chunk;
  return (
    <Sheet
      open={!!sourceId}
      onOpenChange={(o) => !o && openSource(null)}
      title={s.title}
      description={chunk?.file_path}
    >
      <div className="flex items-start gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[13px] font-medium" title={chunk?.file_path}>
            {chunk?.file_path ?? s.loading}
          </div>
          {chunk && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <Badge>{s.lines(chunk.start_line, chunk.end_line)}</Badge>
              {chunk.symbol_name && <Badge variant="accent">{chunk.symbol_name}</Badge>}
              <Badge>{data?.language}</Badge>
            </div>
          )}
        </div>
        {data && (
          <Tooltip label={s.copyPath}>
            <Button
              variant="subtle"
              size="icon-sm"
              aria-label={s.copyPath}
              onClick={() => {
                navigator.clipboard.writeText(`${data.absolute_path}:${chunk!.start_line}`);
                toast.success(strings.chat.copied);
              }}
            >
              <Copy />
            </Button>
          </Tooltip>
        )}
        <Button
          variant="subtle"
          size="icon-sm"
          aria-label={s.close}
          onClick={() => openSource(null)}
        >
          <X />
        </Button>
      </div>

      {data?.stale && (
        <div className="flex items-center gap-2 border-b border-border bg-warning-soft px-4 py-2 text-xs text-warning">
          <AlertTriangle className="size-3.5 shrink-0" />
          {s.stale}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto bg-surface">
        {isLoading && (
          <div className="space-y-2 p-4">
            {Array.from({ length: 12 }, (_, i) => (
              <Skeleton key={i} className="h-4" style={{ width: `${40 + ((i * 37) % 55)}%` }} />
            ))}
          </div>
        )}
        {error && <p className="p-4 text-[13px] text-danger">{(error as Error).message}</p>}
        {data && chunk && (
          <div className="min-w-max py-2 font-mono text-[12.5px] leading-[1.6]">
            {data.lines.map((line, i) => {
              const lineNo = data.first_line + i;
              const marked = lineNo >= chunk.start_line && lineNo <= chunk.end_line;
              return (
                <div
                  key={lineNo}
                  ref={lineNo === chunk.start_line ? firstMarked : undefined}
                  className={cn("flex pr-4", marked && "bg-highlight")}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "w-12 shrink-0 pr-3 text-right text-muted select-none",
                      marked && "border-l-2 border-accent",
                    )}
                  >
                    {lineNo}
                  </span>
                  {html?.[i] !== undefined ? (
                    <span
                      className="shiki whitespace-pre"
                      dangerouslySetInnerHTML={{ __html: html[i] || " " }}
                    />
                  ) : (
                    <span className="whitespace-pre">{line || " "}</span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Sheet>
  );
}
