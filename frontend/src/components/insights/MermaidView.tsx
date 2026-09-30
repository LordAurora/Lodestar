import { Maximize2, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import type { DiagramNode } from "@/lib/api";
import { renderDiagram } from "@/lib/mermaid";
import { strings } from "@/lib/strings";
import { useTheme } from "@/lib/theme";

const s = strings.insights.diagrams;
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 4;

interface View {
  x: number;
  y: number;
  k: number;
}

/**
 * Renders Mermaid text as an SVG you can zoom (wheel or buttons) and pan (drag). When a node
 * has a location in ``nodes``, clicking it (or pressing Enter on it) calls ``onNodeClick``.
 */
export function MermaidView({
  code,
  nodes,
  onNodeClick,
  onRendered,
}: {
  code: string;
  nodes: DiagramNode[];
  onNodeClick: (node: DiagramNode) => void;
  onRendered?: (svg: string | null) => void;
}) {
  const { theme } = useTheme();
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const frame = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; view: View } | null>(null);
  const callback = useRef(onRendered);
  useEffect(() => {
    callback.current = onRendered; // keep the latest callback without re-drawing the diagram
  });

  // Scale the drawing down to fit the frame, and centre it.
  const fit = useCallback(() => {
    const el = frame.current?.querySelector("svg");
    if (!frame.current || !el) return;
    // The drawing's natural size comes from its viewBox, so it does not depend on the zoom.
    const box = el.viewBox.baseVal;
    const width = box.width || el.clientWidth || 1;
    const height = box.height || el.clientHeight || 1;
    el.setAttribute("width", String(width));
    el.setAttribute("height", String(height));
    el.style.maxWidth = "none";
    const k = Math.min(
      1,
      (frame.current.clientWidth - 32) / width,
      (frame.current.clientHeight - 32) / height,
    );
    setView({
      k,
      x: (frame.current.clientWidth - width * k) / 2,
      y: Math.max(16, (frame.current.clientHeight - height * k) / 2),
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    renderDiagram(code, theme === "dark")
      .then((result) => {
        if (cancelled) return;
        setSvg(result);
        setError(null);
        callback.current?.(result);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setSvg(null);
        setError(e.message);
        callback.current?.(null);
      });
    return () => {
      cancelled = true;
    };
  }, [code, theme]);

  // After each new drawing: fit it to the frame and make its nodes clickable.
  useEffect(() => {
    if (!svg || !canvas.current) return;
    requestAnimationFrame(fit);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const cleanups: (() => void)[] = [];
    canvas.current.querySelectorAll<SVGGElement>("g.node").forEach((el) => {
      const match = /(?:flowchart|graph)-(n\d+)-/.exec(el.id);
      const node = match ? byId.get(match[1]) : undefined;
      if (!node || !node.path) return;
      const open = () => onNodeClick(node);
      const onKey = (e: KeyboardEvent) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      };
      el.style.cursor = "pointer";
      el.setAttribute("tabindex", "0");
      el.setAttribute("role", "button");
      el.setAttribute("aria-label", `${s.open} ${node.label}`);
      el.addEventListener("click", open);
      el.addEventListener("keydown", onKey);
      cleanups.push(() => {
        el.removeEventListener("click", open);
        el.removeEventListener("keydown", onKey);
      });
    });
    return () => cleanups.forEach((fn) => fn());
  }, [svg, nodes, onNodeClick, fit]);

  const zoom = (factor: number, cx?: number, cy?: number) =>
    setView((v) => {
      const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.k * factor));
      const rect = frame.current?.getBoundingClientRect();
      const px = cx ?? (rect ? rect.width / 2 : 0);
      const py = cy ?? (rect ? rect.height / 2 : 0);
      return { k, x: px - ((px - v.x) / v.k) * k, y: py - ((py - v.y) / v.k) * k };
    });

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={frame}
        role="img"
        aria-label={s.diagram}
        className="absolute inset-0 cursor-grab touch-none overflow-hidden rounded-lg border border-border bg-surface active:cursor-grabbing"
        onWheel={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - rect.left, e.clientY - rect.top);
        }}
        onPointerDown={(e) => {
          if ((e.target as Element).closest("g.node")) return; // let node clicks through
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x: e.clientX, y: e.clientY, view };
        }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          const { x, y, view: start } = drag.current;
          setView({ ...start, x: start.x + e.clientX - x, y: start.y + e.clientY - y });
        }}
        onPointerUp={() => (drag.current = null)}
      >
        {svg && (
          <div
            ref={canvas}
            style={{
              transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`,
              transformOrigin: "0 0",
            }}
            className="absolute top-0 left-0"
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        )}
        {!svg && !error && (
          <div className="flex h-full items-center justify-center text-[13px] text-muted">
            {s.drawing}
          </div>
        )}
        {error && (
          <div role="alert" className="p-6 text-[13px] text-danger">
            {s.renderFailed}: {error}
          </div>
        )}
      </div>
      <div className="absolute right-3 bottom-3 flex items-center gap-1 rounded-md border border-border bg-background p-1">
        <Tooltip label={s.zoomOut}>
          <Button
            variant="subtle"
            size="icon-sm"
            aria-label={s.zoomOut}
            onClick={() => zoom(1 / 1.25)}
          >
            <Minus />
          </Button>
        </Tooltip>
        <span className="w-10 text-center text-xs text-muted tabular-nums">
          {Math.round(view.k * 100)}%
        </span>
        <Tooltip label={s.zoomIn}>
          <Button variant="subtle" size="icon-sm" aria-label={s.zoomIn} onClick={() => zoom(1.25)}>
            <Plus />
          </Button>
        </Tooltip>
        <Tooltip label={s.fit}>
          <Button variant="subtle" size="icon-sm" aria-label={s.fit} onClick={fit}>
            <Maximize2 />
          </Button>
        </Tooltip>
      </div>
    </div>
  );
}
