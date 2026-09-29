import { RefreshCw } from "lucide-react";
import { LocalOnlyBadge } from "@/components/RuntimeStatus";
import { Badge, Dot } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Repo } from "@/lib/api";
import { strings } from "@/lib/strings";
import { timeAgo } from "@/lib/utils";

const s = strings.topbar;

export function TopBar({
  repo,
  indexing,
  onReindex,
}: {
  repo: Repo;
  indexing: boolean;
  onReindex: () => void;
}) {
  const status = indexing ? (
    <Badge variant="accent">
      <Dot className="animate-pulse" />
      {s.indexing}
    </Badge>
  ) : repo.chunk_count ? (
    <Badge variant="success">
      <Dot />
      {s.indexed(repo.chunk_count, timeAgo(repo.last_indexed_at))}
    </Badge>
  ) : (
    <Badge variant="warning">
      <Dot />
      {s.notIndexed}
    </Badge>
  );

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-5">
      <div className="min-w-0">
        <h1 className="truncate text-[15px] font-semibold" title={repo.path}>
          {repo.name}
        </h1>
      </div>
      <div className="hidden sm:block">{status}</div>
      <div className="ml-auto flex items-center gap-3">
        <LocalOnlyBadge />
        <Button variant="secondary" size="sm" onClick={onReindex} disabled={indexing}>
          <RefreshCw className={indexing ? "animate-spin" : undefined} />
          {s.reindex}
        </Button>
      </div>
    </header>
  );
}
