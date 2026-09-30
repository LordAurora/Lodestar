// Mermaid, bundled locally and loaded only when a diagram is first drawn (it is large).
//
// The theme is set from the app's own design tokens so diagrams match the light and dark
// themes. Mermaid's "base" theme is flat (no gradients or shadows), and `securityLevel: strict`
// makes it sanitize labels, on top of the escaping the backend already does.

type MermaidApi = (typeof import("mermaid"))["default"];

let loader: Promise<MermaidApi> | null = null;
let counter = 0;

export function loadMermaid(): Promise<MermaidApi> {
  loader ??= import("mermaid").then((m) => m.default);
  return loader;
}

const token = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Draw Mermaid text as SVG using the current theme. Throws if the text is not valid. */
export async function renderDiagram(code: string, dark: boolean): Promise<string> {
  const mermaid = await loadMermaid();
  const background = token("--background");
  const surface = token("--surface");
  const surface2 = token("--surface-2");
  const foreground = token("--foreground");
  const border = token("--border-strong");
  const muted = token("--muted");
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    look: "classic",
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
    themeVariables: {
      darkMode: dark,
      background,
      primaryColor: surface,
      primaryTextColor: foreground,
      primaryBorderColor: border,
      lineColor: muted,
      secondaryColor: surface2,
      tertiaryColor: background,
      textColor: foreground,
      // sequence diagrams
      actorBkg: surface,
      actorBorder: border,
      actorTextColor: foreground,
      actorLineColor: muted,
      signalColor: muted,
      signalTextColor: foreground,
      labelBoxBkgColor: surface,
      labelBoxBorderColor: border,
      labelTextColor: foreground,
    },
    flowchart: { htmlLabels: false, curve: "basis", useMaxWidth: false, padding: 12 },
    sequence: { useMaxWidth: false, mirrorActors: false },
  });
  counter += 1;
  const { svg } = await mermaid.render(`lodestar-diagram-${counter}`, code);
  return svg;
}
