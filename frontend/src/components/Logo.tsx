import { cn } from "@/lib/utils";

/**
 * The Lodestar mark: a four-point star on a flat tile. It inverts with the
 * theme (black tile in light mode, white tile in dark mode).
 */
export function Logo({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden
      className={cn("shrink-0 text-foreground", className)}
    >
      <rect width="32" height="32" rx="8" fill="currentColor" />
      <path
        d="M16 6l2.6 7.4L26 16l-7.4 2.6L16 26l-2.6-7.4L6 16l7.4-2.6z"
        className="fill-background"
      />
    </svg>
  );
}
