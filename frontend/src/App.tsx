import { useMutation, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useState } from "react";
import { IndexingCard } from "@/components/IndexingCard";
import { Onboarding } from "@/components/Onboarding";
import { Sidebar } from "@/components/Sidebar";
import { SourceDrawer } from "@/components/SourceDrawer";
import { TopBar } from "@/components/TopBar";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { api, type Repo } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { INSIGHT_NAV } from "@/lib/insights-nav";
import { keys, useIndexProgress, useRepos } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { ChatPage } from "@/pages/ChatPage";
import { InsightsPage } from "@/pages/InsightsPage";

// The Settings page is not needed on first paint: load it on demand.
const SettingsPage = lazy(() =>
  import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);

/** The main area for one repository: top bar plus indexing progress or chat. */
function RepoWorkspace({ repo }: { repo: Repo }) {
  const qc = useQueryClient();
  const [started, setWatching] = useState(false);
  const [showSummary, setShowSummary] = useState(false);
  const watching = started || repo.indexing;

  const { view, insight } = useAppState();
  const progress = useIndexProgress(repo.id, watching, () => {
    setWatching(false);
    qc.invalidateQueries({ queryKey: keys.repos });
    qc.invalidateQueries({ queryKey: keys.insights }); // indexing re-runs the analysis too
  });

  const start = useMutation({
    mutationFn: () => api.startIndex(repo.id),
    onSuccess: () => {
      setShowSummary(!repo.chunk_count); // first index: show the summary card when done
      setWatching(true);
      qc.invalidateQueries({ queryKey: keys.repos });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const indexing = watching;
  const firstIndex = !repo.chunk_count;
  const showCard =
    firstIndex || (showSummary && progress?.status === "done") || progress?.status === "error";

  // A re-index of an already indexed repo runs in the background; tell the user when it ends.
  useEffect(() => {
    if (!firstIndex && progress?.status === "done" && !showSummary) {
      toast.success(
        strings.common.reindexed(repo.name, progress.files_changed, progress.files_deleted),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress?.status]);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <TopBar
        repo={repo}
        indexing={indexing}
        onReindex={() => start.mutate()}
        section={view === "insights" ? INSIGHT_NAV.find((i) => i.id === insight) : undefined}
      />
      {showCard ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <IndexingCard
            progress={indexing || progress ? progress : null}
            onStart={() => start.mutate()}
            onDone={() => setShowSummary(false)}
          />
        </div>
      ) : view === "insights" ? (
        <InsightsPage repo={repo} />
      ) : (
        <ChatPage repo={repo} />
      )}
    </div>
  );
}

export default function App() {
  const { repoId, view, selectRepo } = useAppState();
  const { data: repos, isLoading } = useRepos();
  const repo = repos?.find((r) => r.id === repoId);

  // Forget a selected repo that no longer exists; pick the first one otherwise.
  useEffect(() => {
    if (!repos) return;
    if (repoId && !repo) selectRepo(repos[0]?.id ?? null);
    else if (!repoId && repos.length && view === "chat") selectRepo(repos[0].id);
  }, [repos, repoId, repo, selectRepo, view]);

  let main;
  if (isLoading) {
    main = (
      <div className="flex-1 space-y-4 p-10">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full max-w-2xl" />
      </div>
    );
  } else if (view === "settings") {
    main = (
      <Suspense fallback={<Skeleton className="m-10 h-64 max-w-2xl" />}>
        <SettingsPage />
      </Suspense>
    );
  } else if (view === "add-repo" || !repo) {
    main = (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <Onboarding />
      </div>
    );
  } else {
    main = <RepoWorkspace key={repo.id} repo={repo} />;
  }

  return (
    <div className="flex h-full overflow-hidden bg-background">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2"
      >
        {strings.common.skipToContent}
      </a>
      <Sidebar />
      <main id="main" className="flex min-h-0 min-w-0 flex-1 flex-col">
        {main}
      </main>
      <SourceDrawer />
    </div>
  );
}
