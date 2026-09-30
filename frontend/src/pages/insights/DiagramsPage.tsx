import { AlertCircle, Copy, Download, Network, RefreshCcw } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { InsightHeader } from "@/components/insights/InsightHeader";
import { EmptyState, NotAnalyzed } from "@/components/insights/InsightStates";
import { MermaidView } from "@/components/insights/MermaidView";
import { SymbolPicker } from "@/components/insights/SymbolPicker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectItem } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { toast } from "@/components/ui/toaster";
import type { DiagramNode, DiagramParams, Repo, SymbolHit } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { useDiagram, useDiagramScopes, useEndpoints, useInsightsStatus } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.insights.diagrams;
const ALL = "all";
const NONE = "none";

type Kind = "modules" | "flow";

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { id: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex rounded-md border border-border bg-background p-0.5"
    >
      {options.map((o) => (
        <button
          key={o.id}
          role="radio"
          aria-checked={value === o.id}
          onClick={() => onChange(o.id)}
          className={cn(
            "rounded px-3 py-1 text-[13px] font-medium text-muted transition-colors hover:text-foreground",
            value === o.id && "bg-surface-2 text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function DiagramsPage({ repo }: { repo: Repo }) {
  const { openFile, insightParams } = useAppState();
  const [kind, setKind] = useState<Kind>(
    insightParams.endpointId || insightParams.symbolId ? "flow" : "modules",
  );
  const [scope, setScope] = useState(ALL);
  const [symbol, setSymbol] = useState<SymbolHit | null>(null);
  const [endpointId, setEndpointId] = useState<number | null>(insightParams.endpointId ?? null);
  const [symbolId, setSymbolId] = useState<number | null>(insightParams.symbolId ?? null);
  const [style, setStyle] = useState<"flowchart" | "sequence">("flowchart");
  const [depth, setDepth] = useState(3);
  const [depthDraft, setDepthDraft] = useState(3);
  const [svg, setSvg] = useState<string | null>(null);

  const { data: status } = useInsightsStatus(repo.id);
  const { data: scopes } = useDiagramScopes(repo.id);
  const { data: endpointList } = useEndpoints(repo.id, {});
  const analyzed = status?.analyzed ?? false;

  const params: DiagramParams | null = useMemo(() => {
    if (kind === "modules") return { type: "modules", scope: scope === ALL ? "" : scope };
    const start = symbol?.id ?? symbolId;
    if (start !== null && start !== undefined)
      return { type: "flow", symbol_id: start, depth, style };
    if (endpointId !== null) return { type: "flow", endpoint_id: endpointId, depth, style };
    return null;
  }, [kind, scope, symbol, symbolId, endpointId, depth, style]);

  const { data, error, isFetching } = useDiagram(repo.id, params);
  const onNodeClick = useCallback(
    (node: DiagramNode) => node.path && openFile(node.path, node.line ?? 1),
    [openFile],
  );

  const handlers = (endpointList?.endpoints ?? []).filter((e) => e.handler_symbol_id !== null);
  const legend =
    kind === "flow"
      ? [
          { label: s.legendSolid, dashed: false },
          { label: s.legendDashed, dashed: true },
        ]
      : scope !== ALL
        ? [{ label: s.legendExternal, dashed: true }]
        : [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <InsightHeader repo={repo} title={s.title} subtitle={s.subtitle} icon={Network} />
      {!analyzed && status ? (
        <NotAnalyzed repo={repo} />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-6 py-4">
          {/* Controls */}
          <div className="flex flex-wrap items-end gap-3">
            <Segmented
              label={s.type}
              value={kind}
              onChange={setKind}
              options={[
                { id: "modules", label: s.modules },
                { id: "flow", label: s.flow },
              ]}
            />
            {kind === "modules" ? (
              <div className="w-64">
                <Select value={scope} onValueChange={setScope} label={s.scope}>
                  <SelectItem value={ALL}>{s.wholeRepo}</SelectItem>
                  {(scopes?.folders ?? []).map((folder) => (
                    <SelectItem key={folder} value={folder}>
                      {folder}
                    </SelectItem>
                  ))}
                </Select>
              </div>
            ) : (
              <>
                <div className="w-80">
                  <SymbolPicker
                    repoId={repo.id}
                    value={symbol}
                    onChange={(next) => {
                      setSymbol(next);
                      setSymbolId(null);
                      if (next) setEndpointId(null);
                    }}
                  />
                </div>
                <div className="w-64">
                  <Select
                    value={endpointId === null ? NONE : String(endpointId)}
                    onValueChange={(v) => {
                      setEndpointId(v === NONE ? null : Number(v));
                      setSymbol(null);
                      setSymbolId(null);
                    }}
                    label={s.pickEndpoint}
                  >
                    <SelectItem value={NONE}>{s.orEndpoint}</SelectItem>
                    {handlers.map((e) => (
                      <SelectItem key={e.id} value={String(e.id)}>
                        {e.method} {e.path}
                      </SelectItem>
                    ))}
                  </Select>
                </div>
                <Segmented
                  label={s.style}
                  value={style}
                  onChange={setStyle}
                  options={[
                    { id: "flowchart", label: s.flowchart },
                    { id: "sequence", label: s.sequence },
                  ]}
                />
                <div className="w-40">
                  <div className="mb-1 flex justify-between text-xs text-muted">
                    <span>{s.depth}</span>
                    <span className="tabular-nums">{depthDraft}</span>
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
              </>
            )}
            <div className="ml-auto flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={!data}
                onClick={async () => {
                  await navigator.clipboard.writeText(data!.mermaid);
                  toast.success(s.copied);
                }}
              >
                <Copy />
                {s.copy}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={!svg}
                onClick={() => download(`${kind}.svg`, svg!, "image/svg+xml")}
              >
                <Download />
                {s.downloadSvg}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={!data}
                onClick={() => download(`${kind}.mmd`, data!.mermaid, "text/plain")}
              >
                <Download />
                {s.downloadMmd}
              </Button>
            </div>
          </div>

          {/* Status line */}
          {data && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <Badge>{s.nodes(data.nodes)}</Badge>
              <Badge>{s.links(data.edges)}</Badge>
              {data.level && <span>{s.level(data.level)}</span>}
              {data.cycles && data.cycles.length > 0 && (
                <span className="text-danger">{s.cycles(data.cycles.length)}</span>
              )}
              {data.truncated && <span className="text-warning">{s.truncated}</span>}
              <span className="ml-auto flex items-center gap-3">
                {legend.map((l) => (
                  <span key={l.label} className="flex items-center gap-1.5">
                    <svg width="22" height="6" aria-hidden>
                      <line
                        x1="0"
                        y1="3"
                        x2="22"
                        y2="3"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeDasharray={l.dashed ? "4 3" : undefined}
                      />
                    </svg>
                    {l.label}
                  </span>
                ))}
              </span>
              {isFetching && <RefreshCcw className="size-3 animate-spin" />}
            </div>
          )}

          {/* Canvas */}
          {params === null ? (
            <EmptyState icon={Network} title={s.emptyFlowTitle} body={s.emptyFlowBody} />
          ) : error ? (
            <div role="alert" className="flex items-center gap-2 text-[13px] text-danger">
              <AlertCircle className="size-4" />
              {(error as Error).message}
            </div>
          ) : data && data.nodes === 0 ? (
            <EmptyState
              icon={Network}
              title={s.emptyTitle}
              body={kind === "modules" ? s.emptyModules : s.noFlow}
            />
          ) : data ? (
            <MermaidView
              code={data.mermaid}
              nodes={data.node_index}
              onNodeClick={onNodeClick}
              onRendered={setSvg}
            />
          ) : (
            <div className="min-h-64 flex-1 animate-pulse rounded-lg bg-surface-2" aria-busy />
          )}
        </div>
      )}
    </div>
  );
}
