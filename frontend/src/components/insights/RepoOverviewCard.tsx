import {
  ArrowRight,
  Boxes,
  Copy,
  FileText,
  KeyRound,
  ListChecks,
  Route,
  type LucideIcon,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import type { InsightsStatus, Repo } from "@/lib/api";
import { useAppState, type InsightPage } from "@/lib/app-state";
import { INSIGHT_NAV } from "@/lib/insights-nav";
import { useInsightsStatus } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.insights.overview;

interface Entry {
  key: keyof InsightsStatus["counts"];
  label: string;
  icon: LucideIcon;
  page?: InsightPage;
}

// One entry per number on the overview. An entry appears once the backend reports its count,
// and becomes a link once its page exists.
const ENTRIES: Entry[] = [
  { key: "symbols", label: s.symbols, icon: Boxes },
  { key: "endpoints", label: s.endpoints, icon: Route, page: "endpoints" },
  { key: "env_vars", label: s.env, icon: KeyRound, page: "config" },
  { key: "undocumented", label: s.undocumented, icon: FileText, page: "docs" },
  { key: "duplicate_pairs", label: s.duplicates, icon: Copy, page: "duplicates" },
  { key: "debt_items", label: s.debt, icon: ListChecks, page: "debt" },
];

/** A compact set of counters (symbols, endpoints, env vars, ...), each linking to its page. */
export function RepoOverviewCard({ repo, className }: { repo: Repo; className?: string }) {
  const { data, isLoading } = useInsightsStatus(repo.id);
  const { openInsight } = useAppState();

  if (isLoading) return <Skeleton className={cn("h-20 w-full", className)} />;
  if (!data?.analyzed) return null;
  const available = new Set(INSIGHT_NAV.map((i) => i.id));
  const entries = ENTRIES.filter((e) => data.counts[e.key] !== undefined);

  return (
    <div
      className={cn(
        "grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 lg:grid-cols-3",
        className,
      )}
    >
      {entries.map(({ key, label, icon: Icon, page }) => {
        const linked = page && available.has(page);
        const body = (
          <>
            <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-background">
              <Icon className="size-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-lg leading-tight font-semibold tabular-nums">
                {data.counts[key].toLocaleString()}
              </span>
              <span className="block truncate text-xs text-muted">{label}</span>
            </span>
            {linked && (
              <ArrowRight className="size-4 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
            )}
          </>
        );
        return linked ? (
          <button
            key={key}
            onClick={() => openInsight(page)}
            className="group flex items-center gap-3 bg-surface p-3 text-left transition-colors hover:bg-surface-2"
          >
            {body}
          </button>
        ) : (
          <div key={key} className="flex items-center gap-3 bg-surface p-3">
            {body}
          </div>
        );
      })}
    </div>
  );
}
