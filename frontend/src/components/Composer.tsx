import { ArrowUp, ShieldCheck, Square } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Kbd } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { strings } from "@/lib/strings";

const s = strings.chat;

export interface ComposerHandle {
  focus: () => void;
}

/** The message box docked at the bottom of the chat. */
export const Composer = forwardRef<
  ComposerHandle,
  { onSend: (text: string) => void; onStop: () => void; busy: boolean; disabled?: boolean }
>(function Composer({ onSend, onStop, busy, disabled }, ref) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);

  useImperativeHandle(ref, () => ({ focus: () => area.current?.focus() }));

  // Grow with the content, up to ~8 lines.
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  const send = () => {
    const q = text.trim();
    if (!q || busy || disabled) return;
    onSend(q);
    setText("");
  };

  return (
    <div className="mx-auto w-full max-w-[760px] px-4 pb-4 sm:px-6">
      <div className="flex items-end gap-2 rounded-xl border border-border bg-background p-2 shadow-sm transition-colors focus-within:border-border-strong focus-within:ring-4 focus-within:ring-surface-2">
        <textarea
          ref={area}
          value={text}
          rows={1}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={s.placeholder}
          aria-label={s.placeholder}
          disabled={disabled}
          className="max-h-[200px] min-h-9 flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-muted focus-visible:outline-none"
        />
        {busy ? (
          <Button size="icon" variant="secondary" onClick={onStop} aria-label={s.stop}>
            <Square className="!size-3.5 fill-current" />
          </Button>
        ) : (
          <Button
            size="icon"
            onClick={send}
            disabled={!text.trim() || disabled}
            aria-label={s.send}
          >
            <ArrowUp />
          </Button>
        )}
      </div>
      <div className="mt-2 flex items-center justify-between gap-4 px-1 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          <ShieldCheck className="size-3.5" />
          {s.hint}
        </span>
        <span className="hidden items-center gap-1 sm:flex">
          <Kbd>Enter</Kbd> {s.sendHint}
          <span className="mx-1.5 text-border-strong">·</span>
          <Kbd>Shift</Kbd>
          <Kbd>Enter</Kbd> {s.newLineHint}
          <span className="mx-1.5 text-border-strong">·</span>
          <Kbd>Ctrl</Kbd>
          <Kbd>K</Kbd> {s.focusHint}
        </span>
      </div>
    </div>
  );
});
