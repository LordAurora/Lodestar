import { ChevronRight, FolderGit2, MessagesSquare, RefreshCw, type LucideIcon } from "lucide-react";
import { LocalOnlyBadge } from "@/components/RuntimeStatus";
import { Badge, Dot } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Repo } from "@/lib/api";
import { strings } from "@/lib/strings";
import { timeAgo } from "@/lib/utils";

const s = strings.topbar;

/** Breadcrumb (repo / section), index status, privacy badge and the Re-index action. */
export function TopBar({
  repo,
  indexing,
  onReindex,
  section,
}: {
  repo: Repo;
  indexing: boolean;
  onReindex: () => void;
  /** The page shown below; Chat when omitted. Insights pages render their own heading. */
  section?: { label: string; icon: LucideIcon };
}) {
  const Section = section?.icon ?? MessagesSquare;
  const Heading = section ? "span" : "h1";
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
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background px-5">
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-[13px]">
        <span className="flex min-w-0 items-center gap-2 text-muted" title={repo.path}>
          <FolderGit2 className="size-4 shrink-0" />
          <span className="truncate">{repo.name}</span>
        </span>
        <ChevronRight className="size-3.5 shrink-0 text-border-strong" aria-hidden />
        <Heading className="flex items-center gap-2 font-medium" aria-current="page">
          <Section className="size-4 shrink-0" />
          {section?.label ?? s.chat}
        </Heading>
      </nav>
      <div className="hidden md:block">{status}</div>
      <div className="ml-auto flex items-center gap-2">
        <LocalOnlyBadge />
        <Button variant="secondary" size="sm" onClick={onReindex} disabled={indexing}>
          <RefreshCw className={indexing ? "animate-spin" : undefined} />
          {s.reindex}
        </Button>
      </div>
    </header>
  );
}
