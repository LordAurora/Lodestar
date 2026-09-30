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
const DocsPage = lazy(() =>
  import("@/pages/insights/DocsPage").then((m) => ({ default: m.DocsPage })),
);
const DuplicatesPage = lazy(() =>
  import("@/pages/insights/DuplicatesPage").then((m) => ({ default: m.DuplicatesPage })),
);
const DiagramsPage = lazy(() =>
  import("@/pages/insights/DiagramsPage").then((m) => ({ default: m.DiagramsPage })),
);
const ImpactPage = lazy(() =>
  import("@/pages/insights/ImpactPage").then((m) => ({ default: m.ImpactPage })),
);
const EndpointsPage = lazy(() =>
  import("@/pages/insights/EndpointsPage").then((m) => ({ default: m.EndpointsPage })),
);

/** The Insights area of one repository: picks the page chosen in the sidebar. */
export function InsightsPage({ repo }: { repo: Repo }) {
  const { insight, insightParams } = useAppState();
  return (
    <Suspense fallback={<Skeleton className="m-6 h-64 max-w-3xl" />}>
      {insight === "config" ? (
        <ConfigPage repo={repo} />
      ) : insight === "docs" ? (
        <DocsPage repo={repo} />
      ) : insight === "duplicates" ? (
        <DuplicatesPage repo={repo} />
      ) : insight === "diagrams" ? (
        <DiagramsPage key={JSON.stringify(insightParams)} repo={repo} />
      ) : insight === "impact" ? (
        <ImpactPage repo={repo} />
      ) : insight === "endpoints" ? (
        <EndpointsPage repo={repo} />
      ) : insight === "debt" ? (
        <DebtPage repo={repo} />
      ) : (
        <OverviewPage repo={repo} />
      )}
    </Suspense>
  );
}
