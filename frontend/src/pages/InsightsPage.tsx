import { lazy, Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import type { Repo } from "@/lib/api";
import { useAppState } from "@/lib/app-state";

// Each Insights page is a separate chunk, loaded when it is first opened.
const OverviewPage = lazy(() =>
  import("@/pages/insights/OverviewPage").then((m) => ({ default: m.OverviewPage })),
);
const ConfigPage = lazy(() =>
  import("@/pages/insights/ConfigPage").then((m) => ({ default: m.ConfigPage })),
);
const DebtPage = lazy(() =>
  import("@/pages/insights/DebtPage").then((m) => ({ default: m.DebtPage })),
);

/** The Insights area of one repository: picks the page chosen in the sidebar. */
export function InsightsPage({ repo }: { repo: Repo }) {
  const { insight } = useAppState();
  return (
    <Suspense fallback={<Skeleton className="m-6 h-64 max-w-3xl" />}>
      {insight === "config" ? (
        <ConfigPage repo={repo} />
      ) : insight === "debt" ? (
        <DebtPage repo={repo} />
      ) : (
        <OverviewPage repo={repo} />
      )}
    </Suspense>
  );
}
