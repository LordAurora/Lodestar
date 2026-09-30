import { LayoutDashboard } from "lucide-react";
import { InsightHeader } from "@/components/insights/InsightHeader";
import { NotAnalyzed } from "@/components/insights/InsightStates";
import { RepoOverviewCard } from "@/components/insights/RepoOverviewCard";
import type { Repo } from "@/lib/api";
import { useInsightsStatus } from "@/lib/queries";
import { strings } from "@/lib/strings";

const s = strings.insights;

export function OverviewPage({ repo }: { repo: Repo }) {
  const { data: status } = useInsightsStatus(repo.id);
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <InsightHeader
        repo={repo}
        title={s.title}
        subtitle={s.overview.subtitle}
        icon={LayoutDashboard}
      />
      {status && !status.analyzed ? (
        <NotAnalyzed repo={repo} />
      ) : (
        <div className="mx-auto w-full max-w-4xl px-6 py-6">
          <RepoOverviewCard repo={repo} />
          <p className="mt-4 text-xs text-muted">{s.heuristic}</p>
        </div>
      )}
    </div>
  );
}
