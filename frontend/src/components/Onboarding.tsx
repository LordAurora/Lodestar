import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, FolderGit2 } from "lucide-react";
import { useState } from "react";
import { RuntimeStatus } from "@/components/RuntimeStatus";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { keys, useRepos } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { timeAgo } from "@/lib/utils";

const s = strings.onboarding;

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
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-12">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{s.title}</h1>
        <p className="mt-2 text-muted">{s.subtitle}</p>
      </div>

      <Card>
        <CardContent className="flex flex-col gap-5">
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
              <Input
                id="repo-path"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder={s.pathPlaceholder}
                aria-invalid={add.isError}
                aria-describedby={add.isError ? "repo-path-error" : "repo-path-hint"}
                className="font-mono text-[13px]"
                autoFocus
                spellCheck={false}
              />
              <Button type="submit" disabled={!path.trim() || add.isPending}>
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

          <ol className="grid gap-3 sm:grid-cols-3">
            {s.steps.map((step, i) => (
              <li key={step.title} className="rounded-md border border-border bg-surface p-3">
                <div className="flex items-center gap-2 text-[13px] font-medium">
                  <span className="flex size-5 items-center justify-center rounded-full bg-accent-soft text-xs text-accent-text">
                    {i + 1}
                  </span>
                  {step.title}
                </div>
                <p className="mt-1.5 text-xs text-muted">{step.body}</p>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

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
                      <FolderGit2 className="size-4 shrink-0 text-muted" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-medium">{r.name}</span>
                        <span className="block truncate text-xs text-muted">
                          {strings.common.chunksAgo(r.chunk_count, timeAgo(r.last_indexed_at))}
                        </span>
                      </span>
                      <ArrowRight className="size-4 text-muted opacity-0 group-hover:opacity-100" />
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
