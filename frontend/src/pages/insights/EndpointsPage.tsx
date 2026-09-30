import { AlertCircle, ArrowDown, ArrowUp, Download, Route } from "lucide-react";
import { useDeferredValue, useMemo, useState } from "react";
import { InsightHeader } from "@/components/insights/InsightHeader";
import { EmptyState, ListSkeleton, NotAnalyzed } from "@/components/insights/InsightStates";
import { Badge, Dot } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectItem } from "@/components/ui/select";
import { Tooltip } from "@/components/ui/tooltip";
import { endpointsExportUrl, type Endpoint, type EndpointFilters, type Repo } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { useEndpoints, useInsightsStatus } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.insights.endpoints;
const ALL = "all";

// Flat status colours for the method dots.
const METHOD_COLOR: Record<string, string> = {
  GET: "var(--success)",
  POST: "var(--accent-text)",
  PUT: "var(--warning)",
  PATCH: "var(--warning)",
  DELETE: "var(--danger)",
};

type SortKey = "method" | "path" | "handler" | "location" | "framework";
interface Sort {
  key: SortKey;
  dir: 1 | -1;
}

const sortValue = (e: Endpoint, key: SortKey): string | number => {
  switch (key) {
    case "method":
      return e.method;
    case "path":
      return e.path;
    case "handler":
      return e.handler ?? "";
    case "location":
      return `${e.file_path}:${String(e.line).padStart(6, "0")}`;
    case "framework":
      return e.framework_label;
  }
};

export function MethodBadge({ method }: { method: string }) {
  return (
    <Badge className="min-w-16 justify-center font-mono">
      <span
        style={{ ["--dot" as string]: METHOD_COLOR[method] ?? "var(--muted)" }}
        className="flex"
      >
        <Dot />
      </span>
      {method}
    </Badge>
  );
}

function SortHeader({
  label,
  column,
  sort,
  onSort,
  className,
}: {
  label: string;
  column: SortKey;
  sort: Sort;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const active = sort.key === column;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
      className={cn("px-3 py-2.5 font-medium first:pl-6 last:pr-6", className)}
    >
      <button
        onClick={() => onSort(column)}
        aria-label={s.sortBy(label)}
        className="inline-flex items-center gap-1 hover:text-foreground"
      >
        {label}
        {active &&
          (sort.dir === 1 ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    </th>
  );
}

export function EndpointsPage({ repo }: { repo: Repo }) {
  const { openFile } = useAppState();
  const [search, setSearch] = useState("");
  const [method, setMethod] = useState(ALL);
  const [framework, setFramework] = useState(ALL);
  const [sort, setSort] = useState<Sort>({ key: "path", dir: 1 });
  const q = useDeferredValue(search);

  const filters: EndpointFilters = useMemo(
    () => ({
      q,
      method: method === ALL ? "" : method,
      framework: framework === ALL ? "" : framework,
    }),
    [q, method, framework],
  );
  const { data: status } = useInsightsStatus(repo.id);
  const { data, isLoading, error } = useEndpoints(repo.id, filters);
  const analyzed = status?.analyzed ?? false;

  const rows = useMemo(() => {
    const list = [...(data?.endpoints ?? [])];
    list.sort((a, b) => {
      const x = sortValue(a, sort.key);
      const y = sortValue(b, sort.key);
      return (x < y ? -1 : x > y ? 1 : a.path.localeCompare(b.path)) * sort.dir;
    });
    return list;
  }, [data, sort]);

  const onSort = (key: SortKey) =>
    setSort((current) => ({ key, dir: current.key === key && current.dir === 1 ? -1 : 1 }));

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <InsightHeader
        repo={repo}
        title={s.title}
        subtitle={s.subtitle}
        icon={Route}
        actions={
          data && data.total > 0 ? (
            <>
              {(["json", "csv", "markdown"] as const).map((format) => (
                <Button key={format} asChild variant="secondary" size="sm">
                  <a href={endpointsExportUrl(repo.id, format, filters)} download>
                    <Download />
                    {format === "markdown" ? "Markdown" : format.toUpperCase()}
                  </a>
                </Button>
              ))}
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
      ) : data && data.total === 0 ? (
        <EmptyState icon={Route} title={s.emptyTitle} body={s.emptyBody} />
      ) : data ? (
        <>
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-6 py-3">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={s.search}
              aria-label={s.search}
              className="w-64"
            />
            <div
              role="radiogroup"
              aria-label={s.method}
              className="inline-flex rounded-md border border-border bg-background p-0.5"
            >
              {[{ method: ALL, count: data.total }, ...data.methods].map((m) => (
                <button
                  key={m.method}
                  role="radio"
                  aria-checked={method === m.method}
                  onClick={() => setMethod(m.method)}
                  className={cn(
                    "rounded px-2.5 py-1 font-mono text-xs font-medium text-muted transition-colors hover:text-foreground",
                    method === m.method && "bg-surface-2 text-foreground",
                  )}
                >
                  {m.method === ALL ? s.allMethods : m.method}
                  <span className="ml-1.5 font-sans tabular-nums text-muted">{m.count}</span>
                </button>
              ))}
            </div>
            <div className="w-44">
              <Select value={framework} onValueChange={setFramework} label={s.framework}>
                <SelectItem value={ALL}>{s.allFrameworks}</SelectItem>
                {data.frameworks.map((f) => (
                  <SelectItem key={f.framework} value={f.framework}>
                    {f.label} ({f.count})
                  </SelectItem>
                ))}
              </Select>
            </div>
            <span className="ml-auto text-xs text-muted tabular-nums">
              {s.shown(data.shown, data.total)}
            </span>
          </div>

          {data.shown === 0 ? (
            <EmptyState icon={Route} title={s.noMatches} body={s.noMatchesBody} />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-[13px]">
                <thead className="border-b border-border text-xs text-muted">
                  <tr>
                    <SortHeader label={s.method} column="method" sort={sort} onSort={onSort} />
                    <SortHeader label={s.path} column="path" sort={sort} onSort={onSort} />
                    <SortHeader label={s.handler} column="handler" sort={sort} onSort={onSort} />
                    <SortHeader label={s.location} column="location" sort={sort} onSort={onSort} />
                    <SortHeader
                      label={s.framework}
                      column="framework"
                      sort={sort}
                      onSort={onSort}
                    />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((e) => (
                    <tr key={e.id} className="border-b border-border hover:bg-surface">
                      <td className="py-2.5 pr-3 pl-6">
                        <MethodBadge method={e.method} />
                      </td>
                      <td className="px-3 py-2.5">
                        <button
                          onClick={() => openFile(e.file_path, e.line)}
                          aria-label={s.openHandler(e.path)}
                          className="flex items-center gap-2 text-left"
                        >
                          <code className="font-mono text-[13px] font-medium">{e.path}</code>
                          {e.partial && (
                            <Tooltip label={s.partialHint}>
                              <span>
                                <Badge variant="warning">{s.partial}</Badge>
                              </span>
                            </Tooltip>
                          )}
                        </button>
                      </td>
                      <td className="max-w-[260px] truncate px-3 py-2.5 font-mono text-xs text-muted">
                        {e.handler ?? "-"}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-xs text-muted">
                        {e.file_path}:{e.line}
                      </td>
                      <td className="py-2.5 pr-6 pl-3">
                        <Badge>{e.framework_label}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
