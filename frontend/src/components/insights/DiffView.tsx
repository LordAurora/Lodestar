import type { DiffRow } from "@/lib/api";
import { cn } from "@/lib/utils";

const MARK = { delete: "-", replace: "~", insert: "+", equal: " " } as const;

function Cell({
  text,
  number,
  tone,
  mark,
}: {
  text: string | null;
  number: number | null;
  tone: "remove" | "add" | "none";
  mark: string;
}) {
  return (
    <div
      className={cn(
        "flex min-w-0",
        tone === "remove" && "bg-danger-soft",
        tone === "add" && "bg-success-soft",
        text === null && "bg-surface-2/50",
      )}
    >
      <span aria-hidden className="w-9 shrink-0 pr-2 text-right text-muted select-none">
        {number ?? ""}
      </span>
      <span
        aria-hidden
        className={cn(
          "w-4 shrink-0 text-center select-none",
          tone === "remove" && "text-danger",
          tone === "add" && "text-success",
        )}
      >
        {text === null ? "" : mark}
      </span>
      <span className="whitespace-pre">{text ?? ""}</span>
    </div>
  );
}

/**
 * Two versions of some code, line by line. Removed and added lines carry a `-` or `+` marker
 * as well as a background colour, so the difference does not rely on colour alone.
 */
export function SideBySideDiff({
  rows,
  leftLabel,
  rightLabel,
}: {
  rows: DiffRow[];
  leftLabel: string;
  rightLabel: string;
}) {
  let left = 0;
  let right = 0;
  const numbered = rows.map((row) => ({
    ...row,
    leftNumber: row.left === null ? null : ++left,
    rightNumber: row.right === null ? null : ++right,
  }));

  return (
    <div className="overflow-x-auto rounded-md border border-border bg-background">
      <div className="grid min-w-[640px] grid-cols-2 divide-x divide-border border-b border-border bg-surface text-xs">
        <div className="truncate px-3 py-1.5 font-mono">{leftLabel}</div>
        <div className="truncate px-3 py-1.5 font-mono">{rightLabel}</div>
      </div>
      <div
        role="table"
        aria-label={`${leftLabel} compared with ${rightLabel}`}
        className="grid min-w-[640px] grid-cols-2 divide-x divide-border py-1 font-mono text-[12.5px] leading-[1.6]"
      >
        {numbered.map((row, i) => (
          <div key={i} role="row" className="contents">
            <Cell
              text={row.left}
              number={row.leftNumber}
              tone={row.op === "replace" || row.op === "delete" ? "remove" : "none"}
              mark={row.op === "equal" ? MARK.equal : "-"}
            />
            <Cell
              text={row.right}
              number={row.rightNumber}
              tone={row.op === "replace" || row.op === "insert" ? "add" : "none"}
              mark={row.op === "equal" ? MARK.equal : "+"}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

/** A unified diff (`+`, `-` and `@@` lines) as text with coloured lines. */
export function UnifiedDiff({ text }: { text: string }) {
  return (
    <pre className="overflow-x-auto rounded-md border border-border bg-background py-1 font-mono text-[12.5px] leading-[1.6]">
      {text.split("\n").map((line, i) => (
        <div
          key={i}
          className={cn(
            "px-3 whitespace-pre",
            line.startsWith("+") && !line.startsWith("+++") && "bg-success-soft text-success",
            line.startsWith("-") && !line.startsWith("---") && "bg-danger-soft text-danger",
            line.startsWith("@@") && "bg-surface-2 text-muted",
          )}
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}
