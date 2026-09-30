import {
  Check,
  ChevronRight,
  Copy,
  Download,
  KeyRound,
  Lock,
  Minus,
  ShieldCheck,
} from "lucide-react";
import { Fragment, useDeferredValue, useState } from "react";
import { InsightHeader } from "@/components/insights/InsightHeader";
import { EmptyState, ListSkeleton, NotAnalyzed } from "@/components/insights/InsightStates";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import type { EnvVariable, Repo } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { useEnv, useEnvExample, useInsightsStatus } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.insights.config;
type Tab = "variables" | "example";

function DefaultCell({ variable }: { variable: EnvVariable }) {
  if (variable.is_secret) {
    return <span className="text-muted italic">{s.hidden}</span>;
  }
  if (variable.default_varies) return <span className="text-muted italic">{s.varies}</span>;
  if (variable.default === null) return <span className="text-muted">{s.none}</span>;
  return (
    <code className="rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-xs">
      {variable.default}
    </code>
  );
}

function UsageList({ variable }: { variable: EnvVariable }) {
  const { openFile } = useAppState();
  return (
    <ul className="divide-y divide-border rounded-md border border-border bg-background">
      {variable.usages.map((u) => (
        <li key={`${u.file_path}:${u.line}`}>
          <button
            onClick={() => openFile(u.file_path, u.line)}
            className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-surface-2"
          >
            <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">
              {u.file_path}
              <span className="text-muted">:{u.line}</span>
            </span>
            {u.symbol && <span className="truncate text-xs text-muted">{u.symbol}</span>}
            <Badge>{u.language}</Badge>
            <Badge variant={u.required ? "warning" : "neutral"}>
              {u.required ? s.required : s.optional}
            </Badge>
          </button>
        </li>
      ))}
    </ul>
  );
}

function VariablesTable({ variables }: { variables: EnvVariable[] }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (name: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (!next.delete(name)) next.add(name);
      return next;
    });

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-[13px]">
        <thead className="border-b border-border text-xs text-muted">
          <tr>
            <th className="px-6 py-2.5 font-medium">{s.name}</th>
            <th className="px-3 py-2.5 font-medium">{s.required}</th>
            <th className="px-3 py-2.5 font-medium">{s.default}</th>
            <th className="px-3 py-2.5 font-medium">{s.usages}</th>
            <th className="px-6 py-2.5 font-medium">{s.declared}</th>
          </tr>
        </thead>
        <tbody>
          {variables.map((v) => {
            const expanded = open.has(v.name);
            return (
              <Fragment key={v.name}>
                <tr className={cn("border-b border-border", expanded && "bg-surface")}>
                  <td className="px-6 py-2.5">
                    <button
                      onClick={() => toggle(v.name)}
                      aria-expanded={expanded}
                      className="flex items-center gap-2 text-left"
                    >
                      <ChevronRight
                        className={cn(
                          "size-3.5 text-muted transition-transform",
                          expanded && "rotate-90",
                        )}
                      />
                      <span className="font-mono text-[13px] font-medium">{v.name}</span>
                      {v.is_secret && (
                        <Tooltip label={s.secretHint}>
                          <span>
                            <Badge variant="warning">
                              <Lock />
                              {s.secret}
                            </Badge>
                          </span>
                        </Tooltip>
                      )}
                    </button>
                  </td>
                  <td className="px-3 py-2.5">
                    <Badge variant={v.required ? "warning" : "neutral"}>
                      {v.required ? s.required : s.optional}
                    </Badge>
                  </td>
                  <td className="px-3 py-2.5">
                    <DefaultCell variable={v} />
                  </td>
                  <td className="px-3 py-2.5 tabular-nums">{s.places(v.usage_count)}</td>
                  <td className="px-6 py-2.5">
                    {v.declared_in.length ? (
                      <Tooltip label={s.declaredIn(v.declared_in.join(", "))}>
                        <span className="inline-flex items-center gap-1 text-success">
                          <Check
                            className="size-4"
                            aria-label={s.declaredIn(v.declared_in.join(", "))}
                          />
                        </span>
                      </Tooltip>
                    ) : (
                      <Tooltip label={s.notDeclared}>
                        <span className="inline-flex text-muted">
                          <Minus className="size-4" aria-label={s.notDeclared} />
                        </span>
                      </Tooltip>
                    )}
                  </td>
                </tr>
                {expanded && (
                  <tr className="border-b border-border bg-surface">
                    <td colSpan={5} className="px-6 pt-1 pb-4">
                      <UsageList variable={v} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ExamplePanel({ repo }: { repo: Repo }) {
  const { data, isLoading } = useEnvExample(repo.id);
  if (isLoading || !data) return <ListSkeleton rows={8} />;

  const download = () => {
    const url = URL.createObjectURL(new Blob([data.text], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = ".env.example";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-5">
      <div className="flex flex-wrap items-center gap-3">
        <p className="flex-1 text-[13px] text-muted">{s.exampleHelp}</p>
        <Button
          variant="secondary"
          size="sm"
          onClick={async () => {
            await navigator.clipboard.writeText(data.text);
            toast.success(s.copied);
          }}
        >
          <Copy />
          {s.copy}
        </Button>
        <Button size="sm" onClick={download}>
          <Download />
          {s.download}
        </Button>
      </div>
      <pre
        tabIndex={0}
        aria-label={s.tabExample}
        className="mt-4 max-h-[60vh] overflow-auto rounded-lg border border-border bg-surface p-4 font-mono text-[12.5px] leading-relaxed"
      >
        {data.text}
      </pre>
    </div>
  );
}

export function ConfigPage({ repo }: { repo: Repo }) {
  const [tab, setTab] = useState<Tab>("variables");
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search);
  const { data: status } = useInsightsStatus(repo.id);
  const { data, isLoading, error } = useEnv(repo.id, q);
  const analyzed = status?.analyzed ?? false;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <InsightHeader repo={repo} title={s.title} subtitle={s.subtitle} icon={KeyRound} />
      {!analyzed && status ? (
        <NotAnalyzed repo={repo} />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 border-b border-border px-6 py-3">
            <div
              role="tablist"
              className="inline-flex rounded-md border border-border bg-background p-0.5"
            >
              {(["variables", "example"] as const).map((id) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={tab === id}
                  onClick={() => setTab(id)}
                  className={cn(
                    "rounded px-3 py-1 text-[13px] font-medium text-muted transition-colors hover:text-foreground",
                    tab === id && "bg-surface-2 text-foreground",
                  )}
                >
                  {id === "variables" ? s.tabVariables : s.tabExample}
                </button>
              ))}
            </div>
            {tab === "variables" && (
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={s.search}
                aria-label={s.search}
                className="max-w-xs"
              />
            )}
            <p className="ml-auto flex items-center gap-1.5 text-xs text-muted">
              <ShieldCheck className="size-3.5" />
              {s.privacy}
            </p>
          </div>

          {tab === "example" ? (
            <ExamplePanel repo={repo} />
          ) : isLoading ? (
            <ListSkeleton />
          ) : error ? (
            <p className="p-6 text-[13px] text-danger">{(error as Error).message}</p>
          ) : data && data.variables.length ? (
            <VariablesTable variables={data.variables} />
          ) : (
            <EmptyState title={s.emptyTitle} body={s.emptyBody} icon={KeyRound} />
          )}
        </>
      )}
    </div>
  );
}
