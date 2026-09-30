// The Insights pages, in the order they appear in the sidebar. A page is listed only when
// it exists, so the sidebar never links to something that is not built yet.
import {
  Copy,
  FileText,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  Network,
  Radar,
  Route,
  type LucideIcon,
} from "lucide-react";
import type { InsightPage } from "./app-state";
import { strings } from "./strings";

export interface InsightNavItem {
  id: InsightPage;
  label: string;
  icon: LucideIcon;
}

const n = strings.insights.nav;

export const INSIGHT_NAV: InsightNavItem[] = [
  { id: "overview", label: n.overview, icon: LayoutDashboard },
  { id: "impact", label: n.impact, icon: Radar },
  { id: "endpoints", label: n.endpoints, icon: Route },
  { id: "config", label: n.config, icon: KeyRound },
  { id: "debt", label: n.debt, icon: ListChecks },
];

/** Every page the design calls for; pages join INSIGHT_NAV as they are implemented. */
export const PLANNED_PAGES: Record<Exclude<InsightPage, "overview">, InsightNavItem> = {
  impact: { id: "impact", label: n.impact, icon: Radar },
  diagrams: { id: "diagrams", label: n.diagrams, icon: Network },
  endpoints: { id: "endpoints", label: n.endpoints, icon: Route },
  config: { id: "config", label: n.config, icon: KeyRound },
  docs: { id: "docs", label: n.docs, icon: FileText },
  duplicates: { id: "duplicates", label: n.duplicates, icon: Copy },
  debt: { id: "debt", label: n.debt, icon: ListChecks },
};
