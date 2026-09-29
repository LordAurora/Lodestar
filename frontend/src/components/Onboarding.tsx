import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Binary,
  Blocks,
  FolderGit2,
  FolderPlus,
  Loader2,
  MessagesSquare,
  Search,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { RuntimeStatus } from "@/components/RuntimeStatus";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { keys, useRepos } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { timeAgo } from "@/lib/utils";

const s = strings.onboarding;
const STEP_ICONS: LucideIcon[] = [FolderPlus, Binary, MessagesSquare];
const FEATURE_ICONS: LucideIcon[] = [Blocks, Search, ShieldCheck];

function IconTile({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-background">
      <Icon className="size-4" />
    </span>
  );
}

/** Empty state: add a repository folder, see the three steps and the runtime status. */
export function Onboarding() {
  const [path, setPath] = useState("");
  const { selectRepo } = useAppState();
  const { data: repos = [] } = useRepos();
  const qc = useQueryClient();

  const add = useMutation({
    mutationFn: async () => {
      const repo = await api.addRepo(path);
      if (!repo.chunk_count) await api.startIndex(repo.id); // index right away
      return repo;
    },
    onSuccess: (repo) => {
      qc.invalidateQueries({ queryKey: keys.repos });
      selectRepo(repo.id);
    },
  });

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-6 py-14">
      {/* Hero */}
      <div className="flex flex-col items-start gap-4">
        <Badge>
          <ShieldCheck />
          {s.eyebrow}
        </Badge>
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">{s.title}</h1>
        <p className="max-w-xl text-[15px] text-muted">{s.subtitle}</p>
      </div>

      {/* Add repository */}
      <Card>
        <CardContent className="flex flex-col gap-6 p-6">
          <form
            className="flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (path.trim()) add.mutate();
            }}
          >
            <label htmlFor="repo-path" className="text-[13px] font-medium">
              {s.pathLabel}
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1">
                <FolderGit2 className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
                <Input
                  id="repo-path"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder={s.pathPlaceholder}
                  aria-invalid={add.isError}
                  aria-describedby={add.isError ? "repo-path-error" : "repo-path-hint"}
                  className="pl-9 font-mono text-[13px]"
                  autoFocus
                  spellCheck={false}
                />
              </div>
              <Button type="submit" disabled={!path.trim() || add.isPending}>
                {add.isPending ? <Loader2 className="animate-spin" /> : <FolderPlus />}
                {add.isPending ? s.adding : s.add}
              </Button>
            </div>
            {add.isError ? (
              <p id="repo-path-error" role="alert" className="text-[13px] text-danger">
                {(add.error as Error).message}
              </p>
            ) : (
              <p id="repo-path-hint" className="text-xs text-muted">
                {s.tryExample}
              </p>
            )}
          </form>

          <ol className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
            {s.steps.map((step, i) => (
              <li key={step.title} className="flex flex-col gap-3 bg-surface p-4">
                <div className="flex items-center justify-between">
                  <IconTile icon={STEP_ICONS[i]} />
                  <span className="font-mono text-xs text-muted">0{i + 1}</span>
                </div>
                <div>
                  <div className="text-[13px] font-medium">{step.title}</div>
                  <p className="mt-1 text-xs text-muted">{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      {/* Features */}
      <div className="grid gap-4 sm:grid-cols-3">
        {s.features.map((f, i) => (
          <div key={f.title} className="flex gap-3">
            <IconTile icon={FEATURE_ICONS[i]} />
            <div>
              <div className="text-[13px] font-medium">{f.title}</div>
              <p className="mt-0.5 text-xs text-muted">{f.body}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{s.status}</CardTitle>
          </CardHeader>
          <CardContent className="pt-2">
            <RuntimeStatus />
          </CardContent>
        </Card>

        {repos.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>{s.previous}</CardTitle>
              <CardDescription>{strings.common.repoCount(repos.length)}</CardDescription>
            </CardHeader>
            <CardContent className="pt-3">
              <ul className="-mx-2 space-y-0.5">
                {repos.map((r) => (
                  <li key={r.id}>
                    <button
                      onClick={() => selectRepo(r.id)}
                      className="group flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-surface-2"
                    >
                      <span className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-background text-xs font-semibold uppercase">
                        {r.name.charAt(0)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-medium">{r.name}</span>
                        <span className="block truncate text-xs text-muted">
                          {strings.common.chunksAgo(r.chunk_count, timeAgo(r.last_indexed_at))}
                        </span>
                      </span>
                      <ArrowRight className="size-4 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
                    </button>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
