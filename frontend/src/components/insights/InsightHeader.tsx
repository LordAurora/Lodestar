import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Info, Loader2, RefreshCw, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toaster";
import { api, type Repo } from "@/lib/api";
import { keys, useIndexProgress, useInsightsStatus } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { timeAgo } from "@/lib/utils";

const s = strings.insights;

/**
 * Runs the Insights analysis and reports its progress. The analysis is part of indexing, so
 * it shares the indexing stream: we also treat a running index as "analyzing".
 */
export function useAnalysis(repo: Repo) {
  const qc = useQueryClient();
  const [started, setStarted] = useState(false);
  const running = started || repo.indexing;

  const progress = useIndexProgress(repo.id, running, () => {
    setStarted(false);
    qc.invalidateQueries({ queryKey: keys.insights });
    qc.invalidateQueries({ queryKey: keys.repos });
  });

  const start = useMutation({
    mutationFn: () => api.analyze(repo.id),
    onSuccess: () => {
      setStarted(true);
      qc.invalidateQueries({ queryKey: keys.repos });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  return { running, progress, start: () => start.mutate() };
}

/** Title, the "last analyzed" note and the Re-analyze button shared by every Insights page. */
export function InsightHeader({
  repo,
  title,
  subtitle,
  icon: Icon,
  actions,
}: {
  repo: Repo;
  title: string;
  subtitle: string;
  icon: LucideIcon;
  actions?: ReactNode;
}) {
  const { data: status } = useInsightsStatus(repo.id);
  const { running, progress, start } = useAnalysis(repo);
  const analyzing = progress?.status === "analyzing";
  const pct =
    analyzing && progress.analysis_total
      ? Math.round((progress.analysis_done / progress.analysis_total) * 100)
      : 0;

  return (
    <header className="flex flex-wrap items-start gap-x-6 gap-y-3 border-b border-border px-6 py-5">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-surface">
          <Icon className="size-4" />
        </span>
        <div className="min-w-0">
          <h1 className="text-lg leading-tight font-semibold tracking-tight">{title}</h1>
          <p className="mt-0.5 text-[13px] text-muted">{subtitle}</p>
        </div>
      </div>
      <div className="flex flex-col items-end gap-1.5">
        <div className="flex items-center gap-2">
          {actions}
          <Button variant="secondary" size="sm" onClick={start} disabled={running}>
            {running ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {running ? s.analyzing : s.reanalyze}
          </Button>
        </div>
        <div className="text-xs text-muted" aria-live="polite">
          {analyzing
            ? s.progress(progress.analysis_done, progress.analysis_total)
            : status?.last_analyzed_at
              ? s.lastAnalyzed(timeAgo(status.last_analyzed_at))
              : s.neverAnalyzed}
        </div>
        {analyzing && (
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            aria-label={s.analyzing}
            className="h-1 w-40 overflow-hidden rounded-full bg-surface-2"
          >
            <div
              className="h-full bg-accent transition-[width] duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
      </div>
    </header>
  );
}

/** A small note reminding people that name-based results are heuristic. */
export function HeuristicNote() {
  return (
    <p className="flex items-start gap-2 text-xs text-muted">
      <Info className="mt-0.5 size-3.5 shrink-0" />
      {s.heuristic}
    </p>
  );
}
