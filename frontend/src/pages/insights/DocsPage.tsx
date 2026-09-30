import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  ChevronRight,
  FileText,
  Loader2,
  RotateCw,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { UnifiedDiff } from "@/components/insights/DiffView";
import { InsightHeader } from "@/components/insights/InsightHeader";
import { EmptyState, ListSkeleton, NotAnalyzed } from "@/components/insights/InsightStates";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectItem } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toaster";
import {
  api,
  type AcceptResult,
  type DocSuggestion,
  type MissingDoc,
  type MissingDocsParams,
  type Repo,
} from "@/lib/api";
import {
  keys,
  useDocDiff,
  useDocJob,
  useDocSuggestions,
  useInsightsStatus,
  useMissingDocs,
} from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.insights.docs;
const ALL = "all";
const MIN_LINES = [1, 3, 5, 10];
type Tab = "missing" | "review";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function Progress({ repo }: { repo: Repo }) {
  const { data: job } = useDocJob(repo.id);
  const qc = useQueryClient();
  const status = job?.status;
  useEffect(() => {
    if (status === "done" || status === "error") qc.invalidateQueries({ queryKey: keys.insights });
  }, [status, qc]);

  if (!job || job.status === "idle") return null;
  const percent = job.total ? Math.round((job.done / job.total) * 100) : 0;
  return (
    <div
      role="status"
      className="rounded-lg border border-border bg-surface px-4 py-3 text-[13px]"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        {job.status === "running" && <Loader2 className="size-4 animate-spin" />}
        <span className="font-medium">
          {job.status === "running"
            ? s.generating(job.done, job.total)
            : job.status === "error"
              ? (job.error ?? s.failed)
              : s.jobDone}
        </span>
        {job.failed > 0 && <span className="text-muted">{s.jobFailed(job.failed)}</span>}
        {job.status === "running" && job.current && (
          <span className="min-w-0 truncate font-mono text-xs text-muted">
            {s.generatingNow(job.current)}
          </span>
        )}
      </div>
      {job.status === "running" && (
        <div
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2"
        >
          <div className="h-full bg-accent transition-[width]" style={{ width: `${percent}%` }} />
        </div>
      )}
    </div>
  );
}

function MissingRow({
  item,
  checked,
  onToggle,
}: {
  item: MissingDoc;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <li>
      <label className="flex cursor-pointer items-center gap-3 rounded-md px-2.5 py-2 hover:bg-surface-2">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="size-4 shrink-0 accent-[var(--accent)]"
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[13px]">{item.qualified_name}</span>
          <span className="block truncate font-mono text-[11px] text-muted">
            {item.file_path}:{item.start_line}
          </span>
        </span>
        {item.suggestion && (
          <Badge variant={item.suggestion === "failed" ? "neutral" : "accent"}>
            {item.suggestion === "failed" ? s.failed : s.suggested}
          </Badge>
        )}
        <span className="hidden text-xs text-muted tabular-nums sm:inline">
          {s.callers(item.callers)} · {s.lines(item.lines)}
        </span>
      </label>
    </li>
  );
}

function MissingTab({ repo, onReview }: { repo: Repo; onReview: () => void }) {
  const [search, setSearch] = useState("");
  const [lang, setLang] = useState(ALL);
  const [minLines, setMinLines] = useState(3);
  const [tests, setTests] = useState(false);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const deferredSearch = useDeferredValue(search);
  const qc = useQueryClient();
  const { data: job } = useDocJob(repo.id);
  const running = job?.status === "running";

  const params: MissingDocsParams = useMemo(
    () => ({
      q: deferredSearch,
      lang: lang === ALL ? "" : lang,
      min_lines: minLines,
      include_tests: tests,
    }),
    [deferredSearch, lang, minLines, tests],
  );
  const { data, isLoading, error } = useMissingDocs(repo.id, params);
  const items = data?.items ?? [];
  const chosen = items.filter((i) => picked.has(i.symbol_id));

  const generate = useMutation({
    mutationFn: () =>
      api.suggestDocs(
        repo.id,
        chosen.map((i) => i.symbol_id),
      ),
    onSuccess: () => {
      setPicked(new Set());
      qc.invalidateQueries({ queryKey: ["insights", repo.id, "docs-job"] });
    },
    onError: (e) => toast.error(errorText(e)),
  });

  const toggle = (id: number) =>
    setPicked((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <div className="w-64">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={s.search}
            aria-label={s.search}
          />
        </div>
        <div className="w-44">
          <Select value={lang} onValueChange={setLang} label={s.language}>
            <SelectItem value={ALL}>{s.allLanguages}</SelectItem>
            {Object.keys(data?.languages ?? {}).map((l) => (
              <SelectItem key={l} value={l}>
                {l}
              </SelectItem>
            ))}
          </Select>
        </div>
        <div className="w-44">
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
        <label className="flex items-center gap-2 text-[13px]">
          <Switch checked={tests} onCheckedChange={setTests} />
          {s.includeTests}
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[13px] text-muted">{s.count(data?.total ?? 0)}</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setPicked(new Set(items.map((i) => i.symbol_id)))}
        >
          {s.selectAll}
        </Button>
        {picked.size > 0 && (
          <Button variant="ghost" size="sm" onClick={() => setPicked(new Set())}>
            {s.selectNone}
          </Button>
        )}
        <span className="flex-1" />
        {picked.size > 0 && <span className="text-xs text-muted">{s.selected(picked.size)}</span>}
        <Button
          size="sm"
          disabled={chosen.length === 0 || running || generate.isPending}
          onClick={() => generate.mutate()}
        >
          {generate.isPending || running ? <Loader2 className="animate-spin" /> : <Sparkles />}
          {s.generate(chosen.length)}
        </Button>
        {job?.status === "done" && (
          <Button variant="secondary" size="sm" onClick={onReview}>
            {s.goReview}
          </Button>
        )}
      </div>

      {isLoading ? (
        <ListSkeleton rows={6} />
      ) : error ? (
        <div role="alert" className="flex items-center gap-2 text-[13px] text-danger">
          <AlertCircle className="size-4" />
          {errorText(error)}
        </div>
      ) : items.length === 0 ? (
        <EmptyState icon={FileText} title={s.emptyTitle} body={s.emptyBody} />
      ) : (
        <ul className="rounded-lg border border-border p-1.5">
          {items.map((item) => (
            <MissingRow
              key={item.symbol_id}
              item={item}
              checked={picked.has(item.symbol_id)}
              onToggle={() => toggle(item.symbol_id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function statusVariant(status: DocSuggestion["status"]) {
  return status === "pending" ? "accent" : "neutral";
}

function SuggestionCard({ repo, item }: { repo: Repo; item: DocSuggestion }) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const isPending = item.status === "pending";
  const { data: diff, isLoading } = useDocDiff(repo.id, item.id, open && isPending);
  const refresh = () => qc.invalidateQueries({ queryKey: keys.insights });

  const accept = useMutation({
    mutationFn: () => api.acceptDoc(repo.id, item.id),
    onSuccess: (r) => {
      toast.success(s.applied(1, r.files));
      if (!r.reindexed) toast.message(s.reindexSkipped);
      refresh();
    },
    onError: (e) => {
      toast.error(errorText(e));
      refresh();
    },
  });
  const reject = useMutation({
    mutationFn: () => api.rejectDoc(repo.id, item.id),
    onSuccess: () => {
      toast(s.rejectedToast);
      refresh();
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const regenerate = useMutation({
    mutationFn: () => api.regenerateDoc(repo.id, item.id),
    onSuccess: refresh,
    onError: (e) => toast.error(errorText(e)),
  });
  const busy = accept.isPending || reject.isPending || regenerate.isPending;
  const invalid = Boolean(diff?.error);

  return (
    <li className="rounded-lg border border-border">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        {isPending ? (
          <button
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label={open ? s.hideDiff : s.showDiff}
            className="rounded p-0.5 hover:bg-surface-2"
          >
            <ChevronRight
              className={cn("size-4 text-muted transition-transform", open && "rotate-90")}
            />
          </button>
        ) : (
          <span className="size-5" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[13px]">{item.qualified_name}</span>
          <span className="block truncate font-mono text-[11px] text-muted">
            {item.file_path}:{item.start_line}
          </span>
        </span>
        <Badge variant={statusVariant(item.status)}>{s.status[item.status]}</Badge>
        {isPending && (
          <Button size="sm" disabled={busy || invalid} onClick={() => accept.mutate()}>
            <Check />
            {s.accept}
          </Button>
        )}
        {(isPending || item.status === "failed") && (
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => regenerate.mutate()}>
            <RotateCw />
            {s.regenerate}
          </Button>
        )}
        {isPending && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => reject.mutate()}>
            <X />
            {s.reject}
          </Button>
        )}
      </div>
      {item.status === "failed" && item.error && (
        <p role="alert" className="border-t border-border px-4 py-2.5 text-[13px] text-danger">
          {item.error}
        </p>
      )}
      {open && isPending && (
        <div className="border-t border-border p-3">
          {isLoading ? (
            <div
              className="h-20 animate-pulse rounded-md bg-surface-2"
              aria-label={s.loadingDiff}
            />
          ) : diff?.error ? (
            <p role="alert" className="text-[13px] text-danger">
              {diff.error}
            </p>
          ) : diff ? (
            <UnifiedDiff text={diff.diff} />
          ) : null}
        </div>
      )}
    </li>
  );
}

function ReviewTab({ repo }: { repo: Repo }) {
  const [filter, setFilter] = useState<"pending" | typeof ALL>("pending");
  const [confirm, setConfirm] = useState(false);
  const { data, isLoading, error } = useDocSuggestions(repo.id);
  const qc = useQueryClient();
  const all = data?.items ?? [];
  const pending = all.filter((i) => i.status === "pending");
  const shown = (filter === "pending" ? pending : all).slice().reverse();
  const files = new Set(pending.map((i) => i.file_path)).size;

  const acceptAll = useMutation({
    mutationFn: (): Promise<AcceptResult> =>
      api.acceptDocs(
        repo.id,
        pending.map((i) => i.id),
      ),
    onSuccess: (r) => {
      const ok = r.results.filter((x) => x.status === "accepted").length;
      const bad = r.results.length - ok;
      if (ok) toast.success(s.applied(ok, r.files));
      if (bad) toast.error(s.appliedSome(ok, bad));
      if (ok && !r.reindexed) toast.message(s.reindexSkipped);
      setConfirm(false);
      qc.invalidateQueries({ queryKey: keys.insights });
    },
    onError: (e) => {
      toast.error(errorText(e));
      setConfirm(false);
    },
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="w-44">
          <Select
            value={filter}
            onValueChange={(v) => setFilter(v as typeof filter)}
            label={s.filterStatus}
          >
            <SelectItem value="pending">{s.status.pending}</SelectItem>
            <SelectItem value={ALL}>{strings.insights.duplicates.types.all}</SelectItem>
          </Select>
        </div>
        {data && <span className="text-xs text-muted">{s.styleNote(data.style)}</span>}
        <span className="flex-1" />
        <Button size="sm" disabled={pending.length === 0} onClick={() => setConfirm(true)}>
          <Check />
          {s.acceptAll(pending.length)}
        </Button>
      </div>

      {isLoading ? (
        <ListSkeleton rows={4} />
      ) : error ? (
        <div role="alert" className="flex items-center gap-2 text-[13px] text-danger">
          <AlertCircle className="size-4" />
          {errorText(error)}
        </div>
      ) : shown.length === 0 ? (
        <EmptyState icon={FileText} title={s.reviewEmptyTitle} body={s.reviewEmptyBody} />
      ) : (
        <ul className="flex flex-col gap-3">
          {shown.map((item) => (
            <SuggestionCard key={item.id} repo={repo} item={item} />
          ))}
        </ul>
      )}

      <Dialog open={confirm} onOpenChange={setConfirm}>
        <DialogContent title={s.confirmTitle} description={s.confirmBody(pending.length, files)}>
          <div className="flex justify-end gap-2">
            <DialogClose asChild>
              <Button variant="secondary" size="sm">
                {s.cancel}
              </Button>
            </DialogClose>
            <Button size="sm" disabled={acceptAll.isPending} onClick={() => acceptAll.mutate()}>
              {acceptAll.isPending && <Loader2 className="animate-spin" />}
              {s.confirm}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function DocsPage({ repo }: { repo: Repo }) {
  const [tab, setTab] = useState<Tab>("missing");
  const { data: status } = useInsightsStatus(repo.id);
  const { data: suggestions } = useDocSuggestions(repo.id);
  const pendingCount = suggestions?.items.filter((i) => i.status === "pending").length ?? 0;
  const analyzed = status?.analyzed ?? false;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <InsightHeader repo={repo} title={s.title} subtitle={s.subtitle} icon={FileText} />

      {!analyzed && status ? (
        <NotAnalyzed repo={repo} />
      ) : (
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-6 py-5">
          <div className="flex items-start gap-3 rounded-lg border border-border bg-surface px-4 py-3">
            <ShieldCheck className="mt-0.5 size-4 shrink-0" />
            <div className="text-[13px]">
              <p className="font-medium">{s.banner}</p>
              <p className="text-muted">{s.bannerDetail}</p>
            </div>
          </div>

          <Progress repo={repo} />

          <div
            role="tablist"
            aria-label={s.title}
            className="inline-flex self-start rounded-md border border-border bg-background p-0.5"
          >
            {(["missing", "review"] as const).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={cn(
                  "flex items-center gap-2 rounded px-3 py-1 text-[13px] font-medium text-muted transition-colors hover:text-foreground",
                  tab === t && "bg-surface-2 text-foreground",
                )}
              >
                {s.tabs[t]}
                {t === "review" && pendingCount > 0 && <Badge>{pendingCount}</Badge>}
              </button>
            ))}
          </div>

          {tab === "missing" ? (
            <MissingTab repo={repo} onReview={() => setTab("review")} />
          ) : (
            <ReviewTab repo={repo} />
          )}
        </div>
      )}
    </div>
  );
}
