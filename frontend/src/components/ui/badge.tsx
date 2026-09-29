import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

// Monochrome pills: the status colour only shows in the dot / icon.
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground [&_svg]:size-3 [&_svg]:text-[var(--dot)]",
  {
    variants: {
      variant: {
        neutral: "text-muted [--dot:var(--muted)]",
        accent: "bg-surface-2 [--dot:var(--foreground)]",
        success: "[--dot:var(--success)]",
        warning: "[--dot:var(--warning)]",
        danger: "[--dot:var(--danger)] text-danger",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

/** A small status dot used inside badges; takes the badge's status colour. */
export function Dot({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("size-1.5 shrink-0 rounded-full bg-[var(--dot,currentColor)]", className)}
    />
  );
}

/** A keyboard shortcut hint, e.g. <Kbd>Ctrl</Kbd><Kbd>K</Kbd>. */
export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-border bg-surface px-1 font-sans text-[11px] font-medium text-muted">
      {children}
    </kbd>
  );
}
