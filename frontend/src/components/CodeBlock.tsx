import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { Tooltip } from "@/components/ui/tooltip";
import { highlight } from "@/lib/highlight";
import { strings } from "@/lib/strings";

/** A fenced code block in an answer: highlighted with Shiki, with a copy button. */
export function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    highlight(code, lang)
      .then((h) => !cancelled && setHtml(h))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [code, lang]);

  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="group relative overflow-hidden rounded-md border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5 text-xs text-muted">
        <span className="font-mono">{lang || "text"}</span>
        <Tooltip label={copied ? strings.common.copied : strings.common.copyCode}>
          <button
            onClick={copy}
            aria-label={strings.common.copyCode}
            className="rounded p-1 hover:bg-surface-2 hover:text-foreground"
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          </button>
        </Tooltip>
      </div>
      {html ? (
        <div
          className="overflow-x-auto p-3 font-mono text-[12.5px] leading-relaxed [&_pre]:!m-0"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="overflow-x-auto p-3 font-mono text-[12.5px] leading-relaxed">{code}</pre>
      )}
    </div>
  );
}
