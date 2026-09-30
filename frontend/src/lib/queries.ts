// TanStack Query hooks: one place that knows how server data is fetched and cached.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  api,
  watchIndex,
  type DebtFilters,
  type DiagramParams,
  type DuplicateParams,
  type EndpointFilters,
  type IndexProgress,
} from "./api";

/** What the code drawer is showing: an indexed chunk, or a line range of a file. */
export type SourceTarget =
  | { kind: "chunk"; id: string }
  | { kind: "file"; repoId: string; path: string; start: number; end: number };

export const keys = {
  health: ["health"] as const,
  repos: ["repos"] as const,
  conversations: (repoId: string) => ["conversations", repoId] as const,
  messages: (repoId: string, convId: string) => ["messages", repoId, convId] as const,
  models: ["models"] as const,
  settings: ["settings"] as const,
  chunk: (id: string) => ["chunk", id] as const,
  // Everything under "insights" is invalidated together after an analysis run.
  insights: ["insights"] as const,
  insightsStatus: (repoId: string) => ["insights", repoId, "status"] as const,
  env: (repoId: string, q: string) => ["insights", repoId, "env", q] as const,
  envExample: (repoId: string) => ["insights", repoId, "env-example"] as const,
  debt: (repoId: string, filters: DebtFilters) => ["insights", repoId, "debt", filters] as const,
  endpoints: (repoId: string, filters: EndpointFilters) =>
    ["insights", repoId, "endpoints", filters] as const,
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

export const useSourcePreview = (target: SourceTarget | null) =>
  useQuery({
    queryKey: ["source", target] as const,
    queryFn: () =>
      target!.kind === "chunk"
        ? api.chunk(target!.id)
        : api.file(target!.repoId, target!.path, target!.start, target!.end),
    enabled: !!target,
  });

export const useInsightsStatus = (repoId: string | null) =>
  useQuery({
    queryKey: keys.insightsStatus(repoId ?? ""),
    queryFn: () => api.insightsStatus(repoId!),
    enabled: !!repoId,
  });

export const useEnv = (repoId: string, q: string) =>
  useQuery({ queryKey: keys.env(repoId, q), queryFn: () => api.env(repoId, q) });

export const useEnvExample = (repoId: string) =>
  useQuery({ queryKey: keys.envExample(repoId), queryFn: () => api.envExample(repoId) });

export const useDuplicates = (repoId: string, params: DuplicateParams) =>
  useQuery({
    queryKey: ["insights", repoId, "duplicates", params] as const,
    queryFn: () => api.duplicates(repoId, params),
    placeholderData: (previous) => previous,
  });

export const useDuplicateGroup = (repoId: string, groupId: number | null) =>
  useQuery({
    queryKey: ["insights", repoId, "duplicate-group", groupId] as const,
    queryFn: () => api.duplicateGroup(repoId, groupId!),
    enabled: groupId !== null,
  });

export const useDuplicateDiff = (
  repoId: string,
  groupId: number | null,
  a: number | null,
  b: number | null,
) =>
  useQuery({
    queryKey: ["insights", repoId, "duplicate-diff", groupId, a, b] as const,
    queryFn: () => api.duplicateDiff(repoId, groupId!, a!, b!),
    enabled: groupId !== null && a !== null && b !== null && a !== b,
  });

export const useDiagram = (repoId: string, params: DiagramParams | null) =>
  useQuery({
    queryKey: ["insights", repoId, "diagram", params] as const,
    queryFn: () => api.diagram(repoId, params!),
    enabled: params !== null,
    retry: false,
  });

export const useDiagramScopes = (repoId: string) =>
  useQuery({
    queryKey: ["insights", repoId, "diagram-scopes"] as const,
    queryFn: () => api.diagramScopes(repoId),
  });

export const useSymbols = (repoId: string, q: string) =>
  useQuery({
    queryKey: ["insights", repoId, "symbols", q] as const,
    queryFn: () => api.symbols(repoId, q),
    placeholderData: (previous) => previous,
  });

export const useImpact = (repoId: string, symbolId: number | null, depth: number) =>
  useQuery({
    queryKey: ["insights", repoId, "impact", symbolId, depth] as const,
    queryFn: () => api.impact(repoId, symbolId!, depth),
    enabled: symbolId !== null,
  });

export const useEndpoints = (repoId: string, filters: EndpointFilters) =>
  useQuery({
    queryKey: keys.endpoints(repoId, filters),
    queryFn: () => api.endpoints(repoId, filters),
    placeholderData: (previous) => previous,
  });

/** The tech debt board. While topics are being computed it polls until they are ready. */
export const useDebt = (repoId: string, filters: DebtFilters) =>
  useQuery({
    queryKey: keys.debt(repoId, filters),
    queryFn: () => api.debt(repoId, filters),
    placeholderData: (previous) => previous, // no flicker while typing in the search box
    refetchInterval: (q) => (q.state.data?.topics_pending ? 1500 : false),
  });

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
