// Small UI state shared across the app: which repo and conversation are
// selected, which page is showing, and which source is open in the drawer.
// Server data lives in TanStack Query (see ./queries.ts), not here.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import type { SourceTarget } from "./queries";

export type View = "chat" | "settings" | "add-repo" | "insights";
export type InsightPage =
  "overview" | "impact" | "diagrams" | "endpoints" | "config" | "docs" | "duplicates" | "debt";

interface AppState {
  repoId: string | null;
  conversationId: string | null;
  view: View;
  insight: InsightPage;
  source: SourceTarget | null;
  sidebarCollapsed: boolean;
  selectRepo: (id: string | null) => void;
  selectConversation: (id: string | null) => void;
  setView: (view: View) => void;
  /** Open the Insights area on a given page. */
  openInsight: (page: InsightPage) => void;
  /** Show an indexed chunk (by id) in the code drawer, or close the drawer with null. */
  openSource: (chunkId: string | null) => void;
  /** Show lines of a file in the code drawer. */
  openFile: (path: string, start: number, end?: number) => void;
  toggleSidebar: () => void;
}

const Ctx = createContext<AppState | null>(null);
const REPO_KEY = "lodestar.repo";

function readRepo(): string | null {
  try {
    return localStorage.getItem(REPO_KEY);
  } catch {
    return null;
  }
}

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [repoId, setRepoId] = useState<string | null>(readRepo);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [view, setView] = useState<View>("chat");
  const [insight, setInsight] = useState<InsightPage>("overview");
  const [source, setSource] = useState<SourceTarget | null>(null);
  const [sidebarCollapsed, setCollapsed] = useState(
    () => matchMedia("(max-width: 1023px)").matches,
  );

  // Collapse the sidebar to an icon rail on tablet widths.
  useEffect(() => {
    const mq = matchMedia("(max-width: 1023px)");
    const onChange = () => setCollapsed(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const selectRepo = useCallback((id: string | null) => {
    setRepoId(id);
    setConversationId(null);
    setSource(null);
    setView("chat");
    try {
      if (id) localStorage.setItem(REPO_KEY, id);
      else localStorage.removeItem(REPO_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const selectConversation = useCallback((id: string | null) => {
    setConversationId(id);
    setSource(null);
    setView("chat");
  }, []);

  const openInsight = useCallback((page: InsightPage) => {
    setInsight(page);
    setSource(null);
    setView("insights");
  }, []);

  const openSource = useCallback(
    (chunkId: string | null) => setSource(chunkId ? { kind: "chunk", id: chunkId } : null),
    [],
  );

  const openFile = useCallback(
    (path: string, start: number, end?: number) => {
      if (repoId) setSource({ kind: "file", repoId, path, start, end: end ?? start });
    },
    [repoId],
  );

  return (
    <Ctx.Provider
      value={{
        repoId,
        conversationId,
        view,
        insight,
        source,
        sidebarCollapsed,
        selectRepo,
        selectConversation,
        setView,
        openInsight,
        openSource,
        openFile,
        toggleSidebar: () => setCollapsed((c) => !c),
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useAppState(): AppState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAppState must be used inside AppStateProvider");
  return ctx;
}
