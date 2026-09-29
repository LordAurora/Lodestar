// Syntax highlighting with Shiki, bundled locally.
//
// We use Shiki's "core" API with the JavaScript regex engine and import only
// the languages and themes we need, so the bundle stays small and nothing is
// downloaded at runtime.
import { createHighlighterCore, type HighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

const LANGS: Record<string, () => Promise<unknown>> = {
  python: () => import("shiki/langs/python.mjs"),
  javascript: () => import("shiki/langs/javascript.mjs"),
  typescript: () => import("shiki/langs/typescript.mjs"),
  tsx: () => import("shiki/langs/tsx.mjs"),
  jsx: () => import("shiki/langs/jsx.mjs"),
  go: () => import("shiki/langs/go.mjs"),
  java: () => import("shiki/langs/java.mjs"),
  csharp: () => import("shiki/langs/csharp.mjs"),
  rust: () => import("shiki/langs/rust.mjs"),
  json: () => import("shiki/langs/json.mjs"),
  yaml: () => import("shiki/langs/yaml.mjs"),
  bash: () => import("shiki/langs/bash.mjs"),
  sql: () => import("shiki/langs/sql.mjs"),
  markdown: () => import("shiki/langs/markdown.mjs"),
  html: () => import("shiki/langs/html.mjs"),
  css: () => import("shiki/langs/css.mjs"),
  c: () => import("shiki/langs/c.mjs"),
};

const ALIASES: Record<string, string> = {
  py: "python",
  js: "javascript",
  ts: "typescript",
  cs: "csharp",
  "c#": "csharp",
  sh: "bash",
  shell: "bash",
  yml: "yaml",
  md: "markdown",
  golang: "go",
};

let highlighter: Promise<HighlighterCore> | null = null;
const loaded = new Set<string>();

function getHighlighter() {
  highlighter ??= createHighlighterCore({
    themes: [import("shiki/themes/github-light.mjs"), import("shiki/themes/github-dark.mjs")],
    langs: [],
    engine: createJavaScriptRegexEngine(),
  });
  return highlighter;
}

export function normalizeLang(lang: string | undefined): string | null {
  if (!lang) return null;
  const key = lang.toLowerCase();
  const name = ALIASES[key] ?? key;
  return name in LANGS ? name : null;
}

/**
 * Highlight code and return HTML with both light and dark colours
 * (the dark ones are applied by CSS when the `dark` class is set).
 * Returns null for unknown languages; callers then show plain text.
 */
export async function highlight(code: string, lang: string | undefined): Promise<string | null> {
  const name = normalizeLang(lang);
  if (!name) return null;
  const hl = await getHighlighter();
  if (!loaded.has(name)) {
    const mod = (await LANGS[name]()) as {
      default: Parameters<HighlighterCore["loadLanguage"]>[0];
    };
    await hl.loadLanguage(mod.default);
    loaded.add(name);
  }
  return hl.codeToHtml(code, {
    lang: name,
    themes: { light: "github-light", dark: "github-dark" },
    defaultColor: "light",
  });
}

/** Highlight line by line (for the source drawer, which numbers and marks lines). */
export async function highlightLines(lines: string[], lang: string): Promise<string[] | null> {
  const html = await highlight(lines.join("\n"), lang);
  if (!html) return null;
  const doc = new DOMParser().parseFromString(html, "text/html");
  return Array.from(doc.querySelectorAll(".line")).map((el) => el.innerHTML);
}
