import { Loader2, Play, SearchX, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import type { Repo } from "@/lib/api";
import { strings } from "@/lib/strings";
import { useAnalysis } from "./InsightHeader";

const s = strings.insights;

/** A centered empty state: an icon, a title, one sentence and an optional action. */
export function EmptyState({
  icon: Icon = SearchX,
  title,
  body,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center px-6 py-20 text-center">
      <span className="flex size-10 items-center justify-center rounded-lg border border-border bg-surface">
        <Icon className="size-5 text-muted" />
      </span>
      <h2 className="mt-4 text-[15px] font-semibold">{title}</h2>
      <p className="mt-1.5 text-[13px] text-muted">{body}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/** Shown instead of a page's content when the repository has not been analyzed yet. */
export function NotAnalyzed({ repo }: { repo: Repo }) {
  const { running, start } = useAnalysis(repo);
  const indexed = repo.chunk_count > 0;
  return (
    <EmptyState
      title={s.notAnalyzedTitle}
      body={indexed ? s.notAnalyzedBody : s.notIndexedBody}
      action={
        indexed && (
          <Button onClick={start} disabled={running}>
            {running ? <Loader2 className="animate-spin" /> : <Play />}
            {running ? s.analyzing : s.analyzeNow}
          </Button>
        )
      }
    />
  );
}

/** Loading placeholder for a table or list. */
export function ListSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-6" aria-busy>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-10 animate-pulse rounded-md bg-surface-2" />
      ))}
    </div>
  );
}
