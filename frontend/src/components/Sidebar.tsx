import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronsUpDown,
  FolderPlus,
  MessageSquare,
  MessagesSquare,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  Settings,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Logo } from "@/components/Logo";
import { ThemeIconButton, ThemeSegmented } from "@/components/ThemeToggle";
import { Dot } from "@/components/ui/badge";
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
import { api, type Conversation, type Repo } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { keys, useConversations, useHealth, useRepos } from "@/lib/queries";
import { strings } from "@/lib/strings";
import { cn } from "@/lib/utils";

const s = strings.sidebar;

/** A square with the repo's initial, used as its "avatar". */
function RepoAvatar({ repo, size = "md" }: { repo?: Repo; size?: "sm" | "md" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex shrink-0 items-center justify-center rounded-md border border-border bg-background font-semibold uppercase",
        size === "md" ? "size-7 text-xs" : "size-5 text-[10px]",
      )}
    >
      {repo?.name.charAt(0) ?? <Plus className="size-3.5" />}
    </span>
  );
}

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
    <button
      aria-label={s.switchRepo}
      className="rounded-md p-1 hover:bg-surface-2 data-[state=open]:bg-surface-2"
    >
      <RepoAvatar repo={current} />
    </button>
  ) : (
    <button
      aria-label={s.switchRepo}
      className="flex w-full items-center gap-2.5 rounded-lg border border-border bg-background p-1.5 pr-2.5 text-left transition-colors hover:border-border-strong data-[state=open]:border-border-strong"
    >
      <RepoAvatar repo={current} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] leading-tight font-medium">
          {current?.name ?? s.addRepository}
        </span>
        {current && (
          <span className="block truncate text-[11px] leading-tight text-muted">
            {current.path}
          </span>
        )}
      </span>
      <ChevronsUpDown className="size-3.5 shrink-0 text-muted" />
    </button>
  );

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          <DropdownMenuLabel>{s.repositories}</DropdownMenuLabel>
          {repos.map((r) => (
            <DropdownMenuItem key={r.id} onSelect={() => selectRepo(r.id)}>
              <RepoAvatar repo={r} size="sm" />
              <span className="flex-1 truncate">{r.name}</span>
              {r.id === repoId && <Check />}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setView("add-repo")}>
            <FolderPlus />
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

/** One row in the main navigation (icon + label, or icon only when collapsed). */
function NavItem({
  icon: Icon,
  label,
  active,
  collapsed,
  onClick,
  trailing,
}: {
  icon: LucideIcon;
  label: string;
  active?: boolean;
  collapsed: boolean;
  onClick: () => void;
  trailing?: ReactNode;
}) {
  const button = (
    <button
      onClick={onClick}
      aria-label={collapsed ? label : undefined}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-2.5 rounded-md text-[13px] transition-colors",
        collapsed ? "size-9 justify-center" : "h-8 w-full px-2.5",
        active
          ? "bg-surface-2 font-medium text-foreground"
          : "text-muted hover:bg-surface-2 hover:text-foreground",
      )}
    >
      <Icon className="size-4 shrink-0" />
      {!collapsed && <span className="flex-1 truncate text-left">{label}</span>}
      {!collapsed && trailing}
    </button>
  );
  return collapsed ? (
    <Tooltip label={label} side="right">
      {button}
    </Tooltip>
  ) : (
    button
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
          "flex h-8 w-full items-center gap-2.5 rounded-md pr-8 pl-2.5 text-left text-[13px] transition-colors",
          active
            ? "bg-surface-2 font-medium text-foreground"
            : "text-muted hover:bg-surface-2 hover:text-foreground",
        )}
      >
        <MessageSquare className="size-3.5 shrink-0" />
        <span className="truncate">{conv.title}</span>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={strings.common.moreActions(conv.title)}
            className="absolute top-1/2 right-1 -translate-y-1/2 rounded p-1 text-muted opacity-0 group-hover:opacity-100 hover:bg-background hover:text-foreground focus-visible:opacity-100 data-[state=open]:opacity-100"
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

/** Foundry Local status line shown at the bottom of the sidebar. */
function RuntimeLine({ collapsed }: { collapsed: boolean }) {
  const { data } = useHealth();
  const running = !!data?.foundry.running;
  const starting = !!data && !running && !data.foundry.error;
  const color = running ? "var(--success)" : starting ? "var(--muted)" : "var(--warning)";
  const label = running ? s.running : starting ? s.starting : s.stopped;
  const dot = (
    <span style={{ ["--dot" as string]: color }} className="flex items-center">
      <Dot className={cn("size-2", starting && "animate-pulse")} />
    </span>
  );
  if (collapsed) {
    return (
      <Tooltip label={label} side="right">
        <span role="status" aria-label={label} className="flex size-9 items-center justify-center">
          {dot}
        </span>
      </Tooltip>
    );
  }
  return (
    <div role="status" className="flex items-center gap-2.5 px-2.5 py-1">
      {dot}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium">{label}</span>
        {data?.chat_model.alias && (
          <span className="block truncate font-mono text-[11px] text-muted">
            {data.chat_model.alias}
          </span>
        )}
      </span>
    </div>
  );
}

function SectionLabel({ children, trailing }: { children: ReactNode; trailing?: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-2.5 pb-1.5 text-[11px] font-medium tracking-wide text-muted uppercase">
      {children}
      {trailing}
    </div>
  );
}

export function Sidebar() {
  const {
    repoId,
    conversationId,
    selectConversation,
    view,
    setView,
    sidebarCollapsed: collapsed,
    toggleSidebar,
  } = useAppState();
  const { data: conversations, isLoading } = useConversations(repoId);

  return (
    <aside
      aria-label="Sidebar"
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-150",
        collapsed ? "w-16 items-center" : "w-64",
      )}
    >
      {/* Brand */}
      <div
        className={cn(
          "flex h-14 shrink-0 items-center gap-2.5",
          collapsed ? "justify-center" : "px-4",
        )}
      >
        <Logo size={26} />
        {!collapsed && (
          <>
            <span className="flex-1 text-[15px] font-semibold tracking-tight">
              {strings.appName}
            </span>
            <Tooltip label={s.collapse} side="right">
              <Button
                variant="subtle"
                size="icon-sm"
                onClick={toggleSidebar}
                aria-label={s.collapse}
              >
                <PanelLeftClose />
              </Button>
            </Tooltip>
          </>
        )}
      </div>

      {/* Workspace */}
      <div className={cn("flex flex-col gap-1", collapsed ? "items-center" : "px-3")}>
        {collapsed ? (
          <Tooltip label={s.expand} side="right">
            <Button variant="subtle" size="icon" onClick={toggleSidebar} aria-label={s.expand}>
              <PanelLeftOpen />
            </Button>
          </Tooltip>
        ) : (
          <SectionLabel>{s.workspace}</SectionLabel>
        )}
        <RepoSwitcher collapsed={collapsed} />
      </div>

      {/* Navigation */}
      <nav
        aria-label={strings.appName}
        className={cn("mt-4 flex flex-col gap-0.5", collapsed ? "items-center" : "px-3")}
      >
        {repoId && (
          <NavItem
            icon={Plus}
            label={s.newChat}
            collapsed={collapsed}
            onClick={() => selectConversation(null)}
          />
        )}
        <NavItem
          icon={MessagesSquare}
          label={s.chat}
          collapsed={collapsed}
          active={view === "chat"}
          onClick={() => setView("chat")}
        />
        <NavItem
          icon={Settings}
          label={s.settings}
          collapsed={collapsed}
          active={view === "settings"}
          onClick={() => setView("settings")}
        />
      </nav>

      {/* Conversations */}
      <div className="mt-5 min-h-0 w-full flex-1 overflow-y-auto px-3">
        {!collapsed && repoId && (
          <section aria-label={s.conversations}>
            <SectionLabel
              trailing={
                conversations?.length ? (
                  <span className="tabular-nums">{conversations.length}</span>
                ) : null
              }
            >
              {s.conversations}
            </SectionLabel>
            {isLoading ? (
              <div className="space-y-1.5">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-8" />
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
          </section>
        )}
      </div>

      {/* Footer: runtime status and theme */}
      <div
        className={cn(
          "flex w-full shrink-0 flex-col gap-2 border-t border-border py-3",
          collapsed ? "items-center" : "px-3",
        )}
      >
        <RuntimeLine collapsed={collapsed} />
        {collapsed ? (
          <ThemeIconButton />
        ) : (
          <div className="flex items-center justify-between px-2.5">
            <span className="text-xs text-muted">{strings.theme.label}</span>
            <ThemeSegmented />
          </div>
        )}
      </div>
    </aside>
  );
}
