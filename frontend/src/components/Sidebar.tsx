import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronsUpDown,
  FolderGit2,
  MessageSquare,
  Moon,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  Settings,
  Sun,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import { api, type Conversation } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { keys, useConversations, useRepos } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

const s = strings.sidebar;

function RepoSwitcher({ collapsed }: { collapsed: boolean }) {
  const { repoId, selectRepo, setView } = useAppState();
  const { data: repos = [] } = useRepos();
  const current = repos.find((r) => r.id === repoId);
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteRepo(id),
    onSuccess: () => {
      selectRepo(null);
      setView("add-repo");
      qc.invalidateQueries({ queryKey: keys.repos });
      setConfirm(false);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const trigger = collapsed ? (
    <Button variant="ghost" size="icon" aria-label={s.switchRepo}>
      <FolderGit2 />
    </Button>
  ) : (
    <button
      aria-label={s.switchRepo}
      className="flex w-full items-center gap-2 rounded-md border border-border bg-background px-2.5 py-2 text-left hover:bg-surface-2"
    >
      <FolderGit2 className="size-4 shrink-0 text-muted" />
      <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
        {current?.name ?? s.addRepository}
      </span>
      <ChevronsUpDown className="size-3.5 shrink-0 text-muted" />
    </button>
  );

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-60">
          <DropdownMenuLabel>{s.repositories}</DropdownMenuLabel>
          {repos.map((r) => (
            <DropdownMenuItem key={r.id} onSelect={() => selectRepo(r.id)}>
              <Check className={cn(r.id === repoId ? "opacity-100" : "opacity-0")} />
              <span className="truncate">{r.name}</span>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setView("add-repo")}>
            <Plus />
            {s.addRepository}
          </DropdownMenuItem>
          {current && (
            <DropdownMenuItem destructive onSelect={() => setConfirm(true)}>
              <Trash2 />
              {s.removeRepo}
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={confirm} onOpenChange={setConfirm}>
        {current && (
          <DialogContent title={s.removeRepo} description={s.removeRepoConfirm(current.name)}>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setConfirm(false)}>
                {strings.common.cancel}
              </Button>
              <Button variant="danger" onClick={() => remove.mutate(current.id)}>
                {s.removeRepo}
              </Button>
            </div>
          </DialogContent>
        )}
      </Dialog>
    </>
  );
}

function ConversationItem({ conv, active }: { conv: Conversation; active: boolean }) {
  const { repoId, selectConversation } = useAppState();
  const qc = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [title, setTitle] = useState(conv.title);
  const refresh = () => qc.invalidateQueries({ queryKey: keys.conversations(repoId!) });

  const rename = useMutation({
    mutationFn: () => api.renameConversation(repoId!, conv.id, title),
    onSuccess: () => {
      refresh();
      setRenaming(false);
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteConversation(repoId!, conv.id),
    onSuccess: () => {
      if (active) selectConversation(null);
      refresh();
      setDeleting(false);
    },
  });

  return (
    <li className="group relative">
      <button
        onClick={() => selectConversation(conv.id)}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex w-full items-center rounded-md py-1.5 pr-8 pl-2.5 text-left text-[13px] hover:bg-surface-2",
          active ? "bg-accent-soft font-medium text-accent-text" : "text-foreground",
        )}
      >
        <span className="truncate">{conv.title}</span>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={strings.common.moreActions(conv.title)}
            className="absolute top-1/2 right-1 -translate-y-1/2 rounded p-1 text-muted opacity-0 group-hover:opacity-100 hover:bg-background focus-visible:opacity-100 data-[state=open]:opacity-100"
          >
            <MoreHorizontal className="size-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={() => setRenaming(true)}>
            <Pencil />
            {s.rename}
          </DropdownMenuItem>
          <DropdownMenuItem destructive onSelect={() => setDeleting(true)}>
            <Trash2 />
            {s.delete}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={renaming} onOpenChange={setRenaming}>
        <DialogContent title={s.rename}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (title.trim()) rename.mutate();
            }}
            className="flex flex-col gap-4"
          >
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              autoFocus
              aria-label={s.rename}
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setRenaming(false)}>
                {strings.common.cancel}
              </Button>
              <Button type="submit">{strings.common.save}</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={deleting} onOpenChange={setDeleting}>
        <DialogContent title={s.deleteConfirm} description={conv.title}>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setDeleting(false)}>
              {strings.common.cancel}
            </Button>
            <Button variant="danger" onClick={() => remove.mutate()}>
              {s.delete}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </li>
  );
}

export function Sidebar() {
  const {
    repoId,
    conversationId,
    selectConversation,
    view,
    setView,
    sidebarCollapsed,
    toggleSidebar,
  } = useAppState();
  const { data: conversations, isLoading } = useConversations(repoId);
  const { theme, toggle } = useTheme();
  const collapsed = sidebarCollapsed;

  return (
    <aside
      aria-label="Sidebar"
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-150",
        collapsed ? "w-16 items-center" : "w-60",
      )}
    >
      <div className={cn("flex h-14 items-center gap-2", collapsed ? "justify-center" : "px-4")}>
        <Logo />
        {!collapsed && (
          <span className="flex-1 text-[15px] font-semibold tracking-tight">{strings.appName}</span>
        )}
        {!collapsed && (
          <Tooltip label={s.collapse} side="right">
            <Button variant="subtle" size="icon-sm" onClick={toggleSidebar} aria-label={s.collapse}>
              <PanelLeftClose />
            </Button>
          </Tooltip>
        )}
      </div>

      <div className={cn("flex flex-col gap-2", collapsed ? "items-center" : "px-3")}>
        {collapsed && (
          <Tooltip label={s.expand} side="right">
            <Button variant="subtle" size="icon" onClick={toggleSidebar} aria-label={s.expand}>
              <PanelLeftOpen />
            </Button>
          </Tooltip>
        )}
        <RepoSwitcher collapsed={collapsed} />
        {repoId &&
          (collapsed ? (
            <Tooltip label={s.newChat} side="right">
              <Button
                variant="ghost"
                size="icon"
                onClick={() => selectConversation(null)}
                aria-label={s.newChat}
              >
                <Plus />
              </Button>
            </Tooltip>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              className="justify-start"
              onClick={() => selectConversation(null)}
            >
              <Plus />
              {s.newChat}
            </Button>
          ))}
      </div>

      <nav aria-label={s.conversations} className="mt-4 min-h-0 flex-1 overflow-y-auto px-3">
        {!collapsed && repoId && (
          <>
            <div className="px-2.5 pb-1.5 text-xs font-medium text-muted">{s.conversations}</div>
            {isLoading ? (
              <div className="space-y-1.5">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-7" />
                ))}
              </div>
            ) : conversations?.length ? (
              <ul className="space-y-0.5">
                {conversations.map((c) => (
                  <ConversationItem
                    key={c.id}
                    conv={c}
                    active={view === "chat" && c.id === conversationId}
                  />
                ))}
              </ul>
            ) : (
              <p className="px-2.5 text-xs text-muted">{s.noConversations}</p>
            )}
          </>
        )}
        {collapsed && repoId && conversations?.length ? (
          <MessageSquare className="mx-auto size-4 text-muted" aria-hidden />
        ) : null}
      </nav>

      <div
        className={cn(
          "flex gap-1 border-t border-border py-3",
          collapsed ? "flex-col items-center" : "items-center px-3",
        )}
      >
        <Tooltip label={s.settings} side="right">
          <Button
            variant={view === "settings" ? "secondary" : "ghost"}
            size={collapsed ? "icon" : "sm"}
            className={cn(!collapsed && "flex-1 justify-start")}
            onClick={() => setView(view === "settings" ? "chat" : "settings")}
            aria-label={s.settings}
          >
            <Settings />
            {!collapsed && s.settings}
          </Button>
        </Tooltip>
        <Tooltip label={strings.theme.toggle} side="right">
          <Button variant="ghost" size="icon" onClick={toggle} aria-label={strings.theme.toggle}>
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
        </Tooltip>
      </div>
    </aside>
  );
}
