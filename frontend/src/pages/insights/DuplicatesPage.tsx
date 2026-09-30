import { AlertCircle, ChevronRight, Copy, Download } from "lucide-react";
import { useMemo, useState } from "react";
import { SideBySideDiff } from "@/components/insights/DiffView";
import { InsightHeader } from "@/components/insights/InsightHeader";
import { EmptyState, ListSkeleton, NotAnalyzed } from "@/components/insights/InsightStates";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectItem } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tooltip } from "@/components/ui/tooltip";
import {
  duplicatesExportUrl,
  type DuplicateGroup,
  type DuplicateMember,
  type DuplicateParams,
  type Repo,
} from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import {
  useDuplicateDiff,
  useDuplicateGroup,
  useDuplicates,
  useInsightsStatus,
} from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.insights.duplicates;
const TYPES = ["all", "exact", "similar"] as const;
const MIN_LINES = [3, 5, 8, 12];

function MemberRow({ member }: { member: DuplicateMember }) {
  const { openFile } = useAppState();
  return (
    <li>
      <button
        onClick={() => openFile(member.file_path, member.start_line, member.end_line)}
        className="flex w-full items-center gap-3 rounded-md px-2.5 py-1.5 text-left hover:bg-surface-2"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[13px]">{member.qualified_name}</span>
          <span className="block truncate font-mono text-[11px] text-muted">
            {member.file_path}:{member.start_line}-{member.end_line}
          </span>
        </span>
        {member.is_test && <Badge>{s.inTests}</Badge>}
        <span className="text-xs text-muted tabular-nums">{s.lines(member.lines)}</span>
      </button>
    </li>
  );
}

function Compare({ repoId, group }: { repoId: string; group: DuplicateGroup }) {
  const { data: detail } = useDuplicateGroup(repoId, group.id);
  const members = detail?.members ?? [];
  const [a, setA] = useState<number | null>(null);
  const [b, setB] = useState<number | null>(null);
  const first = a ?? members[0]?.symbol_id ?? null;
  const second = b ?? members[1]?.symbol_id ?? null;
  const { data: diff, isLoading, error } = useDuplicateDiff(repoId, group.id, first, second);

  const picker = (value: number | null, onChange: (id: number) => void, label: string) => (
    <div className="w-72">
      <Select
        value={value === null ? "" : String(value)}
        onValueChange={(v) => onChange(Number(v))}
        label={label}
      >
        {members.map((m) => (
          <SelectItem key={m.symbol_id} value={String(m.symbol_id)}>
            {m.qualified_name} · {m.file_path}
          </SelectItem>
        ))}
      </Select>
    </div>
  );

  return (
    <div className="border-t border-border p-4">
      <h3 className="mb-2 text-[13px] font-semibold">{s.compare}</h3>
      <div className="flex flex-wrap items-center gap-2">
        {picker(first, setA, s.compareA)}
        {picker(second, setB, s.compareB)}
        {diff && (
          <span className="text-xs text-muted">
            {diff.ratio === 1 ? s.identical : s.changed(Math.round(diff.ratio * 100))}
          </span>
        )}
      </div>
      <div className="mt-3">
        {first === second ? (
          <p className="text-[13px] text-muted">{s.pickTwo}</p>
        ) : isLoading ? (
          <div className="h-24 animate-pulse rounded-md bg-surface-2" />
        ) : error ? (
          <p role="alert" className="text-[13px] text-danger">
            {(error as Error).message}
          </p>
        ) : diff ? (
          <SideBySideDiff
            rows={diff.rows}
            leftLabel={`${diff.a.qualified_name} (${diff.a.file_path}:${diff.a.start_line})`}
            rightLabel={`${diff.b.qualified_name} (${diff.b.file_path}:${diff.b.start_line})`}
          />
        ) : null}
      </div>
    </div>
  );
}

function GroupCard({ repoId, group }: { repoId: string; group: DuplicateGroup }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-lg border border-border">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <button
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? s.collapse : s.expand}
          className="rounded p-0.5 hover:bg-surface-2"
        >
          <ChevronRight
            className={cn("size-4 text-muted transition-transform", open && "rotate-90")}
          />
        </button>
        <Tooltip label={s.typeHint[group.type]}>
          <span>
            <Badge variant={group.type === "exact" ? "accent" : "neutral"}>
              {s.typeBadge[group.type]}
            </Badge>
          </span>
        </Tooltip>
        <Badge>{Math.round(group.avg_similarity * 100)}%</Badge>
        <span className="text-[13px] font-medium">{s.members(group.size)}</span>
        <span className="text-xs text-muted">{s.duplicatedLines(group.duplicated_lines)}</span>
        <Tooltip label={s.valueHint}>
          <span className="ml-auto text-xs text-muted tabular-nums">
            {s.value}: {group.value}
          </span>
        </Tooltip>
      </div>
      <ul className="border-t border-border px-2 py-1.5">
        {group.members.map((m) => (
          <MemberRow key={m.symbol_id} member={m} />
        ))}
        {group.more ? (
          <li className="px-2.5 py-1 text-xs text-muted">{s.more(group.more)}</li>
        ) : null}
      </ul>
      {open && <Compare repoId={repoId} group={group} />}
    </li>
  );
}

export function DuplicatesPage({ repo }: { repo: Repo }) {
  const [similarity, setSimilarity] = useState(0.9);
  const [draft, setDraft] = useState(0.9);
  const [type, setType] = useState<(typeof TYPES)[number]>("all");
  const [tests, setTests] = useState(false);
  const [minLines, setMinLines] = useState(5);

  const params: DuplicateParams = useMemo(
    () => ({ min_similarity: similarity, type, include_tests: tests, min_lines: minLines }),
    [similarity, type, tests, minLines],
  );
  const { data: status } = useInsightsStatus(repo.id);
  const { data, isLoading, error } = useDuplicates(repo.id, params);
  const analyzed = status?.analyzed ?? false;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <InsightHeader
        repo={repo}
        title={s.title}
        subtitle={s.subtitle}
        icon={Copy}
        actions={
          data && data.total > 0 ? (
            <Button asChild variant="secondary" size="sm">
              <a href={duplicatesExportUrl(repo.id, params)} download>
                <Download />
                {s.export}
              </a>
            </Button>
          ) : undefined
        }
      />

      {!analyzed && status ? (
        <NotAnalyzed repo={repo} />
      ) : (
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-6 py-5">
          <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
            <div className="w-56">
              <div className="mb-1 flex justify-between text-xs text-muted">
                <span>{s.similarity}</span>
                <span className="tabular-nums">{Math.round(draft * 100)}%</span>
              </div>
              <Slider
                value={draft}
                onValueChange={setDraft}
                onValueCommit={setSimilarity}
                min={data?.floor ?? 0.8}
                max={1}
                step={0.01}
                label={s.similarity}
              />
            </div>
            <div
              role="radiogroup"
              aria-label={s.type}
              className="inline-flex rounded-md border border-border bg-background p-0.5"
            >
              {TYPES.map((t) => (
                <button
                  key={t}
                  role="radio"
                  aria-checked={type === t}
                  onClick={() => setType(t)}
                  className={cn(
                    "rounded px-3 py-1 text-[13px] font-medium text-muted transition-colors hover:text-foreground",
                    type === t && "bg-surface-2 text-foreground",
                  )}
                >
                  {s.types[t]}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-[13px]">
              <Switch checked={tests} onCheckedChange={setTests} id="dup-tests" />
              {s.includeTests}
            </label>
            <div className="w-40">
              <Select
                value={String(minLines)}
                onValueChange={(v) => setMinLines(Number(v))}
                label={s.minLines}
              >
                {MIN_LINES.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {s.minLines}: {n}
                  </SelectItem>
                ))}
              </Select>
            </div>
          </div>

          {data && (
            <p className="flex flex-wrap gap-x-3 text-xs text-muted">
              <span>{s.groups(data.total)}</span>
              <span>{s.duplicatedLines(data.duplicated_lines)}</span>
              <span>{s.floor(data.floor)}</span>
            </p>
          )}

          {isLoading ? (
            <ListSkeleton rows={4} />
          ) : error ? (
            <div role="alert" className="flex items-center gap-2 text-[13px] text-danger">
              <AlertCircle className="size-4" />
              {(error as Error).message}
            </div>
          ) : data && data.total === 0 ? (
            <EmptyState icon={Copy} title={s.emptyTitle} body={s.emptyBody} />
          ) : data ? (
            <ul className="flex flex-col gap-3">
              {data.groups.map((group) => (
                <GroupCard key={group.id} repoId={repo.id} group={group} />
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </div>
  );
}
