// Small UI state shared across the app: which repo and conversation are
// selected, which page is showing, and which source is open in the drawer.
// Server data lives in TanStack Query (see ./queries.ts), not here.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

type View = "chat" | "settings" | "add-repo";

interface AppState {
  repoId: string | null;
  conversationId: string | null;
  view: View;
  sourceId: string | null;
  sidebarCollapsed: boolean;
  selectRepo: (id: string | null) => void;
  selectConversation: (id: string | null) => void;
  setView: (view: View) => void;
  openSource: (chunkId: string | null) => void;
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
  const [sourceId, setSourceId] = useState<string | null>(null);
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
    setSourceId(null);
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
    setSourceId(null);
    setView("chat");
  }, []);

  return (
    <Ctx.Provider
      value={{
        repoId,
        conversationId,
        view,
        sourceId,
        sidebarCollapsed,
        selectRepo,
        selectConversation,
        setView,
        openSource: setSourceId,
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
