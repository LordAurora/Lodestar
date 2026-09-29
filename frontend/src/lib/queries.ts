// TanStack Query hooks: one place that knows how server data is fetched and cached.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, watchIndex, type IndexProgress } from "./api";

export const keys = {
  health: ["health"] as const,
  repos: ["repos"] as const,
  conversations: (repoId: string) => ["conversations", repoId] as const,
  messages: (repoId: string, convId: string) => ["messages", repoId, convId] as const,
  models: ["models"] as const,
  settings: ["settings"] as const,
  chunk: (id: string) => ["chunk", id] as const,
};

export const useHealth = () =>
  useQuery({ queryKey: keys.health, queryFn: api.health, refetchInterval: 5000 });

export const useRepos = () =>
  useQuery({
    queryKey: keys.repos,
    queryFn: api.repos,
    refetchInterval: (q) => (q.state.data?.some((r) => r.indexing) ? 2000 : false),
  });

export const useConversations = (repoId: string | null) =>
  useQuery({
    queryKey: keys.conversations(repoId ?? ""),
    queryFn: () => api.conversations(repoId!),
    enabled: !!repoId,
  });

export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.settings });

export const useModels = () =>
  useQuery({
    queryKey: keys.models,
    queryFn: api.models,
    // Poll while a download or load is running so progress updates.
    refetchInterval: (q) => (q.state.data?.models.some((m) => m.job) ? 1000 : false),
    retry: false,
  });

export const useChunk = (id: string | null) =>
  useQuery({ queryKey: keys.chunk(id ?? ""), queryFn: () => api.chunk(id!), enabled: !!id });

/** Live indexing progress for a repo while `active` is true (Server-Sent Events). */
export function useIndexProgress(repoId: string | null, active: boolean, onFinish: () => void) {
  const [progress, setProgress] = useState<IndexProgress | null>(null);

  useEffect(() => {
    if (!repoId || !active) return;
    // A new run starts: drop the previous run's numbers before events arrive.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setProgress(null);
    return watchIndex(repoId, (event, data) => {
      if (event === "idle") return;
      setProgress(data);
      if (event !== "progress") onFinish();
    });
    // onFinish is intentionally not a dependency: it changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, active]);

  return progress;
}
