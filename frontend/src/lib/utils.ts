import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { strings } from "./strings";

/** Merge Tailwind class names, letting later classes win. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** "2 min ago" style relative time from a Unix timestamp in seconds. */
export function timeAgo(seconds: number | null | undefined): string {
  if (!seconds) return strings.time.never;
  const diff = Math.max(0, Date.now() / 1000 - seconds);
  if (diff < 60) return strings.time.justNow;
  if (diff < 3600) return strings.time.minutes(Math.floor(diff / 60));
  if (diff < 86400) return strings.time.hours(Math.floor(diff / 3600));
  return strings.time.days(Math.floor(diff / 86400));
}

/** Remove <think>…</think> blocks that reasoning models emit. */
export function stripReasoning(text: string): string {
  return text.replace(/<think>[\s\S]*?(<\/think>|$)/g, "").trimStart();
}
