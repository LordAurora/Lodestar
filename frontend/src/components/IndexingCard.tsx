import { AlertCircle, CheckCircle2, Loader2, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { IndexProgress } from "@/lib/api";
import { strings } from "@/lib/strings";

const s = strings.indexing;

function Counter({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2.5">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}

/** Progress card shown while a repository is (first) indexed. */
export function IndexingCard({
  progress,
  onStart,
  onDone,
}: {
  progress: IndexProgress | null;
  onStart: () => void;
  onDone: () => void;
}) {
  const status = progress?.status ?? "pending";
  const failed = status === "error";
  const done = status === "done";

  // Scanning is quick and embedding dominates; the Insights analysis is the last 10%.
  const scanPct = progress?.files_total ? progress.files_scanned / progress.files_total : 0;
  const embedPct = progress?.chunks_created
    ? progress.chunks_embedded / progress.chunks_created
    : 0;
  const analysisPct = progress?.analysis_total
    ? progress.analysis_done / progress.analysis_total
    : 0;
  const pct = done
    ? 100
    : Math.round(
        status === "analyzing"
          ? 90 + analysisPct * 10
          : status === "embedding"
            ? 20 + embedPct * 70
            : scanPct * 20,
      );

  const title = failed
    ? s.failed
    : done
      ? s.done
      : status === "analyzing"
        ? s.analyzing
        : status === "embedding"
          ? s.embedding
          : s.scanning;

  return (
    <div className="mx-auto w-full max-w-2xl px-6 py-12">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            {failed ? (
              <AlertCircle className="size-4 text-danger" />
            ) : done ? (
              <CheckCircle2 className="size-4 text-success" />
            ) : (
              <Loader2 className="size-4 animate-spin text-accent-text" />
            )}
            <CardTitle>{progress ? title : s.title}</CardTitle>
          </div>
          {failed && <CardDescription className="text-danger">{progress?.error}</CardDescription>}
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            aria-label={s.title}
            className="h-2 overflow-hidden rounded-full bg-surface-2"
          >
            <div
              className={
                failed ? "h-full bg-danger" : "h-full bg-accent transition-[width] duration-300"
              }
              style={{ width: `${pct}%` }}
            />
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Counter
              label={s.files}
              value={`${progress?.files_scanned ?? 0}/${progress?.files_total ?? 0}`}
            />
            <Counter label={s.chunks} value={progress?.chunks_created ?? 0} />
            <Counter label={s.embedded} value={progress?.chunks_embedded ?? 0} />
            <Counter label={s.elapsed} value={`${(progress?.elapsed ?? 0).toFixed(1)}s`} />
          </div>

          {progress && progress.recent_files.length > 0 && (
            <div>
              <div className="mb-1.5 text-xs font-medium text-muted">{s.recent}</div>
              <ul
                aria-live="polite"
                className="max-h-40 overflow-y-auto rounded-md border border-border bg-surface p-2 font-mono text-xs text-muted"
              >
                {[...progress.recent_files].reverse().map((f) => (
                  <li key={f} className="truncate py-0.5">
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(done || failed || !progress) && (
            <div className="flex justify-end">
              {done ? (
                <Button onClick={onDone}>{s.startChatting}</Button>
              ) : (
                <Button onClick={onStart}>
                  <Play />
                  {failed ? strings.chat.retry : strings.topbar.reindex}
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
