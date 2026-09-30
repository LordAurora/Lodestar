import { AlertCircle, Loader2, Radar, Sparkles, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ConfidenceBadge } from "@/components/insights/ConfidenceBadge";
import { HeuristicNote, InsightHeader } from "@/components/insights/InsightHeader";
import { EmptyState, ListSkeleton, NotAnalyzed } from "@/components/insights/InsightStates";
import { SymbolPicker } from "@/components/insights/SymbolPicker";
import { Badge, Dot } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Tooltip } from "@/components/ui/tooltip";
import {
  streamImpactSummary,
  type ImpactItem,
  type ImpactReport,
  type Repo,
  type SymbolHit,
} from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { useImpact, useInsightsStatus, useSymbols } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.insights.impact;

const LEVEL_COLOR = {
  Low: "var(--success)",
  Medium: "var(--warning)",
  High: "var(--danger)",
} as const;

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function ImpactRow({ item }: { item: ImpactItem }) {
  const { openFile } = useAppState();
  const place = `${item.call_file}:${item.call_line}`;
  return (
    <li>
      <button
        onClick={() => openFile(item.call_file, item.call_line)}
        aria-label={s.calledAt(place)}
        className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left hover:bg-surface-2"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[13px]">{item.qualified_name}</span>
          <span className="block truncate font-mono text-[11px] text-muted">{place}</span>
        </span>
        <Badge>{item.kind}</Badge>
        {item.is_test && <Badge>{s.isTest}</Badge>}
        <ConfidenceBadge confidence={item.confidence} />
      </button>
    </li>
  );
}

function Section({
  title,
  help,
  count,
  children,
}: {
  title: string;
  help?: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border">
      <header className="flex items-baseline gap-2 border-b border-border px-4 py-2.5">
        <h2 className="text-[13px] font-semibold">{title}</h2>
        <Badge>{count}</Badge>
        {help && <span className="truncate text-xs text-muted">{help}</span>}
      </header>
      <ul className="p-1">{children}</ul>
    </section>
  );
}

/** Streams the model's plain-language summary, or shows the deterministic fallback. */
function RiskSummary({ repoId, report }: { repoId: string; report: ImpactReport }) {
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "running" | "done">("idle");
  const [fallback, setFallback] = useState(false);
  const abort = useRef<AbortController | null>(null);

  // The parent gives this component a new `key` for every symbol and depth, so a stale
  // summary is dropped by remounting; here we only stop a stream that is still running.
  useEffect(() => () => abort.current?.abort(), []);

  const run = async () => {
    abort.current?.abort();
    const controller = (abort.current = new AbortController());
    setText("");
    setFallback(false);
    setState("running");
    try {
      await streamImpactSummary(
        repoId,
        report.target.id,
        report.depth,
        (e) => {
          if (e.event === "token") setText((t) => t + e.data.text);
          else {
            setText(e.data.text);
            setFallback(e.data.fallback);
            setState("done");
          }
        },
        controller.signal,
      );
    } finally {
      if (!controller.signal.aborted)
        setState((current) => (current === "running" ? "done" : current));
    }
  };

  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center gap-2">
        <Sparkles className="size-4 text-muted" />
        <h2 className="flex-1 text-[13px] font-semibold">{s.summaryTitle}</h2>
        <Button size="sm" variant="secondary" onClick={run} disabled={state === "running"}>
          {state === "running" ? <Loader2 className="animate-spin" /> : <Sparkles />}
          {state === "running" ? s.explaining : s.explain}
        </Button>
      </div>
      {(text || state === "running") && (
        <p
          className={cn(
            "mt-3 text-[13px] leading-relaxed",
            state === "running" && "streaming-caret",
          )}
          aria-live="polite"
        >
          {text}
        </p>
      )}
      {state === "done" && fallback && (
        <p className="mt-2 text-xs text-muted">{s.summaryFallback}</p>
      )}
    </div>
  );
}

function Report({ repo, report }: { repo: Repo; report: ImpactReport }) {
  const { openFile } = useAppState();
  const t = report.target;
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border border-border p-4">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate font-mono text-[15px] font-semibold">
                {t.qualified_name}
              </span>
              <Badge>{t.kind}</Badge>
            </div>
            <button
              onClick={() => openFile(t.file_path, t.line, t.end_line)}
              className="mt-1 font-mono text-xs text-muted hover:text-foreground hover:underline"
            >
              {t.file_path}:{t.line}
            </button>
          </div>
          <Tooltip label={report.formula}>
            <div className="flex items-center gap-3 rounded-md border border-border px-4 py-2">
              <span style={{ ["--dot" as string]: LEVEL_COLOR[report.level] }} className="flex">
                <Dot className="size-2.5" />
              </span>
              <div>
                <div className="text-xs text-muted">{s.blastRadius}</div>
                <div className="text-lg leading-tight font-semibold">
                  {s.blast[report.level]}{" "}
                  <span className="text-xs font-normal text-muted">
                    {s.scoreLabel(report.score)}
                  </span>
                </div>
              </div>
            </div>
          </Tooltip>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label={s.direct} value={report.direct} />
          <Stat label={s.affected} value={report.total} />
          <Stat label={s.files} value={report.files} />
          <Stat label={s.tests} value={report.tests.length} />
        </div>
      </div>

      {report.unresolved_warning && (
        <div
          role="note"
          className="flex items-start gap-2 rounded-md border border-border bg-warning-soft px-3 py-2 text-[13px] text-warning"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          {s.unresolved(report.low_confidence, report.possible_unresolved)}
        </div>
      )}

      <RiskSummary key={`${t.id}-${report.depth}`} repoId={repo.id} report={report} />

      {report.total === 0 && report.module_level.length === 0 ? (
        <EmptyState icon={Radar} title={s.noneTitle} body={s.none(report.depth)} />
      ) : (
        <>
          {report.levels.map((level) => (
            <Section key={level.depth} title={s.levelTitle(level.depth)} count={level.items.length}>
              {level.items.map((item) => (
                <ImpactRow key={`${item.id}-${item.call_line}`} item={item} />
              ))}
            </Section>
          ))}
          {report.tests.length > 0 && (
            <Section title={s.testsTitle} help={s.testsHelp} count={report.tests.length}>
              {report.tests.map((item) => (
                <ImpactRow key={`t-${item.id}`} item={item} />
              ))}
            </Section>
          )}
          {report.module_level.length > 0 && (
            <Section title={s.moduleTitle} help={s.moduleHelp} count={report.module_level.length}>
              {report.module_level.map((use) => (
                <li key={`${use.file_path}:${use.line}`}>
                  <button
                    onClick={() => openFile(use.file_path, use.line)}
                    className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left hover:bg-surface-2"
                  >
                    <span className="flex-1 truncate font-mono text-[13px]">
                      {use.file_path}:{use.line}
                    </span>
                    <ConfidenceBadge confidence={use.confidence} />
                  </button>
                </li>
              ))}
            </Section>
          )}
          {report.truncated && <p className="text-xs text-muted">{s.truncated}</p>}
        </>
      )}
    </div>
  );
}

export function ImpactPage({ repo }: { repo: Repo }) {
  const [selected, setSelected] = useState<SymbolHit | null>(null);
  const [depth, setDepth] = useState(3);
  const [depthDraft, setDepthDraft] = useState(3);
  const { data: status } = useInsightsStatus(repo.id);
  const { data: popular } = useSymbols(repo.id, "");
  const { data: report, isLoading, error } = useImpact(repo.id, selected?.id ?? null, depth);
  const analyzed = status?.analyzed ?? false;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <InsightHeader repo={repo} title={s.title} subtitle={s.subtitle} icon={Radar} />
      {!analyzed && status ? (
        <NotAnalyzed repo={repo} />
      ) : (
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-6 py-5">
          <div className="grid gap-4 sm:grid-cols-[1fr_260px] sm:items-end">
            <SymbolPicker repoId={repo.id} value={selected} onChange={setSelected} />
            <div>
              <div className="mb-1 flex justify-between text-xs text-muted">
                <span>{s.depth}</span>
                <span className="tabular-nums">{s.depthValue(depthDraft)}</span>
              </div>
              <Slider
                value={depthDraft}
                onValueChange={setDepthDraft}
                onValueCommit={setDepth}
                min={1}
                max={5}
                step={1}
                label={s.depth}
              />
            </div>
          </div>
          <HeuristicNote />

          {!selected ? (
            <EmptyState
              icon={Radar}
              title={s.emptyTitle}
              body={s.emptyBody}
              action={
                popular && popular.symbols.length > 0 ? (
                  <div className="flex flex-wrap justify-center gap-2">
                    {popular.symbols.slice(0, 6).map((symbol) => (
                      <button
                        key={symbol.id}
                        onClick={() => setSelected(symbol)}
                        className="rounded-full border border-border px-3 py-1 font-mono text-xs hover:border-border-strong"
                      >
                        {symbol.qualified_name}
                      </button>
                    ))}
                  </div>
                ) : undefined
              }
            />
          ) : isLoading ? (
            <ListSkeleton />
          ) : error ? (
            <div role="alert" className="flex items-center gap-2 text-[13px] text-danger">
              <AlertCircle className="size-4" />
              {(error as Error).message}
            </div>
          ) : report ? (
            <Report repo={repo} report={report} />
          ) : null}
        </div>
      )}
    </div>
  );
}
