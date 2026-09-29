import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Database, KeyRound, Network, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Composer, type ComposerHandle } from "@/components/Composer";
import { Logo } from "@/components/Logo";
import { AssistantMessage, UserMessage, type DisplayMessage } from "@/components/MessageView";
import { Skeleton } from "@/components/ui/skeleton";
import { api, streamChat, type Message, type Repo } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { keys } from "@/lib/queries";
import { strings } from "@/lib/strings";

const s = strings.chat;
const SUGGESTION_ICONS: LucideIcon[] = [KeyRound, Database, Network];

function fromServer(m: Message): DisplayMessage {
  const question = m.role === "assistant" ? undefined : m.content;
  return {
    key: `m${m.id}`,
    role: m.role,
    content: m.content,
    status: m.meta.no_answer ? "no_answer" : "done",
    sources: m.meta.sources ?? [],
    citations: m.meta.citations ?? [],
    query: m.meta.query !== question ? m.meta.query : undefined,
    suggestions: m.meta.suggestions,
  };
}

export function ChatPage({ repo }: { repo: Repo }) {
  const { conversationId, selectConversation } = useAppState();
  const qc = useQueryClient();
  const [thread, setThread] = useState<DisplayMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const ownConversation = useRef<string | null>(null); // conversation created by our own stream
  const composer = useRef<ComposerHandle>(null);
  const bottom = useRef<HTMLDivElement>(null);

  // Load the thread when the user switches conversation (not when our own
  // stream just created it: we already have those messages on screen).
  /* eslint-disable react-hooks/set-state-in-effect -- the thread mirrors server data */
  useEffect(() => {
    if (conversationId && conversationId === ownConversation.current) return;
    abort.current?.abort();
    setBusy(false);
    if (!conversationId) {
      setThread([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    qc.fetchQuery({
      queryKey: keys.messages(repo.id, conversationId),
      queryFn: () => api.messages(repo.id, conversationId),
      staleTime: 0,
    })
      .then((msgs) => {
        if (cancelled) return;
        const display = msgs.map(fromServer);
        // Show the rewritten query only if it differs from the question asked.
        display.forEach((m, i) => {
          if (m.role === "assistant" && m.query === display[i - 1]?.content) m.query = undefined;
        });
        setThread(display);
      })
      .catch(() => !cancelled && setThread([]))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [conversationId, repo.id, qc]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Ctrl/Cmd+K focuses the composer from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        composer.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [thread]);

  const updateLast = (
    patch: Partial<DisplayMessage> | ((m: DisplayMessage) => Partial<DisplayMessage>),
  ) =>
    setThread((t) => {
      const last = t[t.length - 1];
      const next = typeof patch === "function" ? patch(last) : patch;
      return [...t.slice(0, -1), { ...last, ...next }];
    });

  const ask = useCallback(
    async (question: string, regenerate = false) => {
      const controller = new AbortController();
      abort.current = controller;
      setBusy(true);
      const draft: DisplayMessage = {
        key: `draft${Date.now()}`,
        role: "assistant",
        content: "",
        status: "searching",
        sources: [],
        citations: [],
      };
      setThread((t) => {
        const base = regenerate ? t.slice(0, -2) : t;
        const user: DisplayMessage = {
          ...draft,
          key: `u${Date.now()}`,
          role: "user",
          content: question,
          status: "done",
        };
        return [...base, user, draft];
      });

      // Tokens can arrive faster than the screen refreshes. Collect them and
      // apply at most once per animation frame, so long answers stay smooth.
      let pending = "";
      let frame = 0;
      const flush = () => {
        cancelAnimationFrame(frame);
        frame = 0;
        if (!pending) return;
        const text = pending;
        pending = "";
        updateLast((m) => ({ content: m.content + text }));
      };

      try {
        await streamChat(
          repo.id,
          { question, conversation_id: conversationId, regenerate },
          (e) => {
            switch (e.event) {
              case "retrieval":
                if (e.data.conversation_id !== conversationId) {
                  ownConversation.current = e.data.conversation_id;
                  selectConversation(e.data.conversation_id);
                  qc.invalidateQueries({ queryKey: keys.conversations(repo.id) });
                }
                updateLast({
                  sources: e.data.chunks,
                  query: e.data.query !== question ? e.data.query : undefined,
                  status: "streaming",
                });
                break;
              case "token":
                pending += e.data.text;
                frame ||= requestAnimationFrame(flush);
                break;
              case "done":
                flush();
                updateLast({ content: e.data.answer, citations: e.data.citations, status: "done" });
                break;
              case "no_answer":
                updateLast({ status: "no_answer", suggestions: e.data.suggestions });
                break;
              case "error":
                flush();
                updateLast({ status: "error", error: e.data.message });
                break;
            }
          },
          controller.signal,
        );
        // Stream ended without a final event (e.g. the user pressed Stop).
        flush();
        updateLast((m) =>
          m.status === "streaming" || m.status === "searching" ? { status: "done" } : {},
        );
      } catch (err) {
        updateLast({ status: "error", error: (err as Error).message });
      } finally {
        setBusy(false);
        qc.invalidateQueries({ queryKey: keys.conversations(repo.id) });
      }
    },
    [repo.id, conversationId, qc, selectConversation],
  );

  const regenerate = () => {
    const lastQuestion = [...thread].reverse().find((m) => m.role === "user");
    if (lastQuestion && !busy) ask(lastQuestion.content, !!conversationId);
  };

  const empty = !loading && thread.length === 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[760px] px-4 py-8 sm:px-6">
          {loading && (
            <div className="space-y-6">
              <Skeleton className="ml-auto h-10 w-2/3" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
              <Skeleton className="h-4 w-4/6" />
            </div>
          )}

          {empty && (
            <div className="flex flex-col items-center pt-[10vh] text-center">
              <Logo size={40} />
              <h2 className="mt-5 text-2xl font-semibold tracking-tight">
                {s.emptyTitle(repo.name)}
              </h2>
              <p className="mt-2 text-muted">{s.emptySubtitle}</p>
              <div className="mt-8 grid w-full gap-3 text-left sm:grid-cols-3">
                {s.suggestions.map((item, i) => {
                  const Icon = SUGGESTION_ICONS[i];
                  return (
                    <button
                      key={item.question}
                      onClick={() => ask(item.question)}
                      className="group flex flex-col gap-3 rounded-lg border border-border bg-background p-4 transition-colors hover:border-border-strong hover:bg-surface"
                    >
                      <div className="flex items-center justify-between">
                        <span className="flex size-8 items-center justify-center rounded-md border border-border bg-surface">
                          <Icon className="size-4" />
                        </span>
                        <ArrowUpRight className="size-4 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
                      </div>
                      <div>
                        <div className="text-[13px] font-medium">{item.title}</div>
                        <div className="mt-1 text-xs text-muted">{item.question}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <div className="space-y-6">
            {thread.map((m, i) =>
              m.role === "user" ? (
                <UserMessage key={m.key} message={m} />
              ) : (
                <AssistantMessage
                  key={m.key}
                  message={m}
                  isLast={i === thread.length - 1 && !busy}
                  onRegenerate={regenerate}
                />
              ),
            )}
          </div>
          <div ref={bottom} />
        </div>
      </div>
      <Composer
        ref={composer}
        busy={busy}
        onSend={(q) => ask(q)}
        onStop={() => abort.current?.abort()}
        disabled={!repo.chunk_count}
      />
    </div>
  );
}
