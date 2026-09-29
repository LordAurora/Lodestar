import { AlertCircle, Check, Copy, Info, Loader2, RotateCcw, Search } from "lucide-react";
import { useState } from "react";
import { Logo } from "@/components/Logo";
import { Markdown } from "@/components/Markdown";
import { SourceRow, SourcesList } from "@/components/SourcesList";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import type { Citation, Source } from "@/lib/api";
import { useAppState } from "@/lib/app-state";
import { strings } from "@/lib/strings";
import { stripReasoning } from "@/lib/utils";

const s = strings.chat;

/** A message as the chat thread shows it (persisted or still streaming). */
export interface DisplayMessage {
  key: string;
  role: "user" | "assistant";
  content: string;
  status: "searching" | "streaming" | "done" | "no_answer" | "error";
  sources: Source[];
  citations: Citation[];
  query?: string;
  suggestions?: Source[];
  error?: string;
}

export function UserMessage({ message }: { message: DisplayMessage }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-surface-2 px-4 py-2.5 whitespace-pre-wrap">
        {message.content}
      </div>
    </div>
  );
}

/** Neutral (not an error) card shown when nothing relevant was found. */
function NoAnswerCard({ suggestions }: { suggestions: Source[] }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center gap-2 text-[13px] font-medium">
        <Info className="size-4 text-muted" />
        {strings.noAnswer.title}
      </div>
      <p className="mt-1.5 text-[13px] text-muted">{strings.noAnswer.body}</p>
      {suggestions.length > 0 && (
        <>
          <div className="mt-4 mb-1 text-xs font-medium text-muted">{strings.noAnswer.closest}</div>
          <ul className="-mx-1">
            {suggestions.map((src) => (
              <SourceRow
                key={src.chunk_id}
                source={src}
                note={strings.noAnswer.score(src.cosine)}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function AssistantBody({
  message,
  isLast,
  onRegenerate,
}: {
  message: DisplayMessage;
  isLast: boolean;
  onRegenerate: () => void;
}) {
  const { openSource } = useAppState();
  const [copied, setCopied] = useState(false);
  const text = stripReasoning(message.content);

  if (message.status === "no_answer")
    return <NoAnswerCard suggestions={message.suggestions ?? []} />;

  if (message.status === "error") {
    return (
      <div role="alert" className="rounded-lg border border-danger/30 bg-danger-soft p-4">
        <div className="flex items-center gap-2 text-[13px] font-medium text-danger">
          <AlertCircle className="size-4" />
          {s.errorTitle}
        </div>
        <p className="mt-1.5 text-[13px] text-foreground">{message.error}</p>
        {isLast && (
          <Button variant="secondary" size="sm" className="mt-3" onClick={onRegenerate}>
            <RotateCcw />
            {s.retry}
          </Button>
        )}
      </div>
    );
  }

  // Sources panel: the cited chunks, or (if the model cited nothing) everything retrieved.
  const cited = message.citations
    .map((c) => ({ n: c.n, source: message.sources[c.n - 1] }))
    .filter((c) => c.source);
  const listed = cited.length ? cited : message.sources.map((source, i) => ({ n: i + 1, source }));

  const openCitation = (n: number) => {
    const src = message.sources[n - 1];
    if (src) openSource(src.chunk_id);
  };

  return (
    <div className="flex flex-col gap-3">
      {message.status === "searching" ? (
        <div className="flex items-center gap-2 text-[13px] text-muted" role="status">
          <Search className="size-4 animate-pulse" />
          {s.thinking}
        </div>
      ) : (
        <div
          className={message.status === "streaming" && text ? "streaming-caret" : undefined}
          aria-live={message.status === "streaming" ? "polite" : undefined}
          aria-busy={message.status === "streaming"}
        >
          {text ? (
            <Markdown text={text} sources={message.sources} onCite={openCitation} />
          ) : (
            <span className="flex items-center gap-2 text-[13px] text-muted">
              <Loader2 className="size-4 animate-spin" />
              {s.generating}
            </span>
          )}
        </div>
      )}

      {message.status === "done" && (
        <>
          <SourcesList
            title={cited.length ? s.sources(cited.length) : s.retrieved(listed.length)}
            sources={listed.map((l) => l.source)}
            numbers={listed.map((l) => l.n)}
          />
          <div className="-ml-2 flex items-center gap-1">
            <Tooltip label={copied ? s.copied : s.copy}>
              <Button
                variant="subtle"
                size="icon-sm"
                aria-label={s.copy}
                onClick={async () => {
                  await navigator.clipboard.writeText(text);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                }}
              >
                {copied ? <Check /> : <Copy />}
              </Button>
            </Tooltip>
            {isLast && (
              <Tooltip label={s.regenerate}>
                <Button
                  variant="subtle"
                  size="icon-sm"
                  aria-label={s.regenerate}
                  onClick={onRegenerate}
                >
                  <RotateCcw />
                </Button>
              </Tooltip>
            )}
            {message.query && (
              <span className="ml-2 truncate text-xs text-muted">{s.rewritten(message.query)}</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Assistant turn: the Lodestar mark and name, then the answer. */
export function AssistantMessage(props: {
  message: DisplayMessage;
  isLast: boolean;
  onRegenerate: () => void;
}) {
  return (
    <div className="flex gap-3.5">
      <Logo size={26} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 text-[13px] font-semibold">{s.assistant}</div>
        <AssistantBody {...props} />
      </div>
    </div>
  );
}
