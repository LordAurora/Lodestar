import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "@/components/CodeBlock";
import { Tooltip } from "@/components/ui/tooltip";
import type { Source } from "@/lib/api";
import { strings } from "@/lib/strings";

/**
 * Turn citation markers like [1] or [1, 2] into links (#cite-1) that we
 * render as chips. Fenced code blocks are left alone.
 */
function linkCitations(text: string, max: number): string {
  return text
    .split(/(```[\s\S]*?(?:```|$))/g)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(/\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g, (whole, nums: string) => {
            const valid = nums
              .split(",")
              .map((n) => parseInt(n, 10))
              .filter((n) => n >= 1 && n <= max);
            return valid.length ? valid.map((n) => `[${n}](#cite-${n})`).join("") : whole;
          }),
    )
    .join("");
}

export const CitationChip = ({
  n,
  source,
  onOpen,
}: {
  n: number;
  source?: Source;
  onOpen: (n: number) => void;
}) => (
  <Tooltip
    label={
      source
        ? `${source.file_path}:${source.start_line}-${source.end_line}`
        : strings.common.source(n)
    }
  >
    <button
      onClick={() => onOpen(n)}
      aria-label={strings.common.openSource(n, source?.file_path)}
      className="mx-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-accent-soft px-1.5 align-[1px] text-[11px] font-medium text-accent-text hover:bg-accent hover:text-accent-foreground"
    >
      {n}
    </button>
  </Tooltip>
);

export const Markdown = memo(function Markdown({
  text,
  sources,
  onCite,
}: {
  text: string;
  sources: Source[];
  onCite: (n: number) => void;
}) {
  return (
    <div className="prose-answer">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            const match = href?.match(/^#cite-(\d+)$/);
            if (match) {
              const n = Number(match[1]);
              return <CitationChip n={n} source={sources[n - 1]} onOpen={onCite} />;
            }
            // Answers are local; never navigate away with an external link.
            return (
              <span className="text-accent-text underline underline-offset-2">{children}</span>
            );
          },
          pre: ({ children }) => <>{children}</>,
          code: ({ className, children }) => {
            const lang = /language-([\w#+-]+)/.exec(className ?? "")?.[1];
            const code = String(children ?? "");
            const isBlock = !!lang || code.includes("\n");
            return isBlock ? (
              <CodeBlock code={code.replace(/\n$/, "")} lang={lang} />
            ) : (
              <code>{children}</code>
            );
          },
          img: () => null, // no remote images
        }}
      >
        {linkCitations(text, sources.length)}
      </ReactMarkdown>
    </div>
  );
});
