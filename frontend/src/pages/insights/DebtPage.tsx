import { AlertCircle, Download, ListChecks, Loader2, RotateCw } from "lucide-react";
import { useDeferredValue, useMemo, useState } from "react";
import { InsightHeader } from "@/components/insights/InsightHeader";
import { EmptyState, ListSkeleton, NotAnalyzed } from "@/components/insights/InsightStates";
import { Badge, Dot } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectItem } from "@/components/ui/select";
import { toast } from "@/components/ui/toaster";
import {
  api,
  debtExportUrl,
  type DebtFilters,
  type DebtGroup,
  type DebtGroupBy,
  type DebtItem,
  type Repo,
} from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { useDebt, useInsightsStatus } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.insights.debt;
const ALL = "all";
const GROUPINGS: DebtGroupBy[] = ["tag", "folder", "topic", "age"];
const AGES = [30, 180, 365];

// Flat status colours for the tag dots (semantic tokens, no gradients).
const TAG_COLOR: Record<string, string> = {
  TODO: "var(--muted)",
  NOTE: "var(--accent-text)",
  FIXME: "var(--warning)",
  HACK: "var(--warning)",
  XXX: "var(--danger)",
  BUG: "var(--danger)",
};

function TagDot({ tag }: { tag: string }) {
  return (
    <span style={{ ["--dot" as string]: TAG_COLOR[tag] ?? "var(--muted)" }} className="flex">
      <Dot className="size-2" />
    </span>
  );
}

function DebtCard({ item, showTag }: { item: DebtItem; showTag: boolean }) {
  const { openFile } = useAppState();
  const who = item.author ?? item.assignee;
  return (
    <li>
      <button
        onClick={() => openFile(item.file_path, item.line)}
        className="flex w-full flex-col gap-2 rounded-md border border-border bg-background p-3 text-left transition-colors hover:border-border-strong"
      >
        <span className="flex items-start gap-2">
          {showTag && (
            <Badge>
              <TagDot tag={item.tag} />
              {item.tag}
            </Badge>
          )}
          <span className={cn("line-clamp-3 text-[13px]", !item.text && "text-muted italic")}>
            {item.text || "(no text)"}
          </span>
        </span>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          <span className="min-w-0 truncate font-mono">
            {item.file_path}:{item.line}
          </span>
          {item.symbol && <span className="truncate">{item.symbol}</span>}
          {who && <span>{s.by(who)}</span>}
          {item.age_days !== null && <span>{s.ago(item.age_days)}</span>}
        </span>
      </button>
    </li>
  );
}

function Column({ group, showTag }: { group: DebtGroup; showTag: boolean }) {
  return (
    <section
      aria-label={group.label}
      className="flex max-h-full w-[300px] shrink-0 flex-col rounded-lg border border-border bg-surface"
    >
      <header className="flex items-center gap-2 px-3 py-2.5">
        {showTag ? <TagDot tag={group.key} /> : null}
        <h2 className="min-w-0 flex-1 truncate text-[13px] font-semibold">{group.label}</h2>
        <Badge>{group.items.length}</Badge>
      </header>
      <ul className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-2 pb-2">
        {group.items.length ? (
          group.items.map((item) => <DebtCard key={item.id} item={item} showTag={!showTag} />)
        ) : (
          <li className="px-2 py-4 text-center text-xs text-muted">{s.emptyColumn}</li>
        )}
      </ul>
    </section>
  );
}

export function DebtPage({ repo }: { repo: Repo }) {
  const [search, setSearch] = useState("");
  const [folder, setFolder] = useState("");
  const [tag, setTag] = useState(ALL);
  const [age, setAge] = useState(ALL);
  const [groupBy, setGroupBy] = useState<DebtGroupBy>("tag");
  const deferredSearch = useDeferredValue(search);
  const deferredFolder = useDeferredValue(folder);

  const filters: DebtFilters = useMemo(
    () => ({
      group_by: groupBy,
      q: deferredSearch,
      folder: deferredFolder.trim(),
      tag: tag === ALL ? "" : tag,
      older_than_days: age === ALL ? 0 : Number(age),
    }),
    [groupBy, deferredSearch, deferredFolder, tag, age],
  );

  const { data: status } = useInsightsStatus(repo.id);
  const { data, isLoading, error, refetch } = useDebt(repo.id, filters);
  const analyzed = status?.analyzed ?? false;
  const tags = data ? Object.keys(data.counters.by_tag) : [];
  const oldest = data?.counters.oldest;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <InsightHeader
        repo={repo}
        title={s.title}
        subtitle={s.subtitle}
        icon={ListChecks}
        actions={
          data && data.counters.total > 0 ? (
            <>
              <Button asChild variant="secondary" size="sm">
                <a href={debtExportUrl(repo.id, "markdown", filters)} download>
                  <Download />
                  {s.exportMd}
                </a>
              </Button>
              <Button asChild variant="secondary" size="sm">
                <a href={debtExportUrl(repo.id, "csv", filters)} download>
                  <Download />
                  {s.exportCsv}
                </a>
              </Button>
            </>
          ) : undefined
        }
      />

      {!analyzed && status ? (
        <NotAnalyzed repo={repo} />
      ) : isLoading ? (
        <ListSkeleton />
      ) : error ? (
        <div role="alert" className="m-6 flex items-center gap-2 text-[13px] text-danger">
          <AlertCircle className="size-4" />
          {(error as Error).message}
        </div>
      ) : data && data.counters.total === 0 ? (
        <EmptyState icon={ListChecks} title={s.emptyTitle} body={s.emptyBody} />
      ) : data ? (
        <>
          {/* Summary */}
          <div className="flex flex-wrap items-stretch gap-3 px-6 pt-4">
            <div className="rounded-md border border-border bg-surface px-3 py-2">
              <div className="text-xs text-muted">{s.total}</div>
              <div className="text-lg font-semibold tabular-nums">{data.counters.total}</div>
            </div>
            {oldest && (
              <div className="min-w-0 max-w-xs rounded-md border border-border bg-surface px-3 py-2">
                <div className="text-xs text-muted">{s.oldest}</div>
                <div className="truncate text-[13px] font-medium">
                  {s.ago(oldest.age_days ?? 0)} · {oldest.text || oldest.tag}
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-1.5">
              {tags.map((t) => (
                <button
                  key={t}
                  onClick={() => setTag(tag === t ? ALL : t)}
                  aria-pressed={tag === t}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs font-medium hover:border-border-strong",
                    tag === t && "border-foreground bg-surface-2",
                  )}
                >
                  <TagDot tag={t} />
                  {t}
                  <span className="text-muted tabular-nums">{data.counters.by_tag[t]}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2 px-6 py-3">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={s.search}
              aria-label={s.search}
              className="w-56"
            />
            <Input
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              placeholder={s.folder}
              aria-label={s.folder}
              className="w-40 font-mono text-[13px]"
            />
            <div className="w-40">
              <Select value={age} onValueChange={setAge} label={s.allAges}>
                <SelectItem value={ALL}>{s.allAges}</SelectItem>
                {AGES.map((days) => (
                  <SelectItem key={days} value={String(days)}>
                    {s.older(days)}
                  </SelectItem>
                ))}
              </Select>
            </div>
            <div
              role="radiogroup"
              aria-label={s.groupBy}
              className="ml-auto inline-flex rounded-md border border-border bg-background p-0.5"
            >
              {GROUPINGS.map((g) => (
                <button
                  key={g}
                  role="radio"
                  aria-checked={groupBy === g}
                  onClick={() => setGroupBy(g)}
                  className={cn(
                    "rounded px-3 py-1 text-[13px] font-medium text-muted transition-colors hover:text-foreground",
                    groupBy === g && "bg-surface-2 text-foreground",
                  )}
                >
                  {s.groups[g]}
                </button>
              ))}
            </div>
            <span className="text-xs text-muted tabular-nums">
              {s.shown(data.shown, data.counters.total)}
            </span>
          </div>

          {groupBy === "topic" && (data.topics_pending || data.topics_error) && (
            <div
              role="status"
              className="mx-6 mb-3 flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-muted"
            >
              {data.topics_pending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <AlertCircle className="size-4 text-warning" />
              )}
              <span className="flex-1">{data.topics_pending ? s.grouping : s.groupingFailed}</span>
              {data.topics_error && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={async () => {
                    try {
                      await api.refreshDebtTopics(repo.id);
                      refetch();
                    } catch (e) {
                      toast.error((e as Error).message);
                    }
                  }}
                >
                  <RotateCw />
                  {s.regroup}
                </Button>
              )}
            </div>
          )}
          {!data.counters.has_dates && <p className="mx-6 mb-3 text-xs text-muted">{s.noGit}</p>}

          {/* Board */}
          {data.shown === 0 ? (
            <EmptyState title={s.noMatches} body={s.noMatchesBody} icon={ListChecks} />
          ) : (
            <div className="flex min-h-0 flex-1 gap-4 overflow-x-auto px-6 pb-6">
              {data.groups.map((group) => (
                <Column key={group.key} group={group} showTag={groupBy === "tag"} />
              ))}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
