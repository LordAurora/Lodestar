import { Badge, Dot } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import type { Confidence } from "@/lib/api";
import { strings } from "@/lib/strings";

const s = strings.insights.impact.confidence;

const COLOR: Record<Confidence, string> = {
  high: "var(--success)",
  medium: "var(--warning)",
  low: "var(--danger)",
};
const HINT: Record<Confidence, string> = {
  high: s.highHint,
  medium: s.mediumHint,
  low: s.lowHint,
};

/** How sure the analysis is about a link found by matching names (never shown as fact). */
export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  return (
    <Tooltip label={HINT[confidence]}>
      <span>
        <Badge>
          <span style={{ ["--dot" as string]: COLOR[confidence] }} className="flex">
            <Dot />
          </span>
          {s[confidence]}
        </Badge>
      </span>
    </Tooltip>
  );
}
