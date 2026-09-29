import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { strings } from "@/lib/strings";
import { useTheme, type ThemeMode } from "@/lib/theme";
import { cn } from "@/lib/utils";

const OPTIONS: { mode: ThemeMode; icon: typeof Sun; label: string }[] = [
  { mode: "light", icon: Sun, label: strings.theme.light },
  { mode: "dark", icon: Moon, label: strings.theme.dark },
  { mode: "system", icon: Monitor, label: strings.theme.system },
];

/** Segmented Light / Dark / System control (like Vercel's footer switcher). */
export function ThemeSegmented({ size = "sm" }: { size?: "sm" | "md" }) {
  const { mode, setMode } = useTheme();
  return (
    <div
      role="radiogroup"
      aria-label={strings.theme.label}
      className="inline-flex items-center gap-0.5 rounded-full border border-border bg-background p-0.5"
    >
      {OPTIONS.map(({ mode: m, icon: Icon, label }) => {
        const active = mode === m;
        return (
          <Tooltip key={m} label={label}>
            <button
              role="radio"
              aria-checked={active}
              aria-label={label}
              onClick={() => setMode(m)}
              className={cn(
                "inline-flex items-center justify-center gap-1.5 rounded-full text-muted transition-colors hover:text-foreground",
                size === "sm" ? "size-6" : "h-8 px-3 text-[13px]",
                active && "bg-surface-2 text-foreground",
              )}
            >
              <Icon className={size === "sm" ? "size-3.5" : "size-4"} />
              {size === "md" && label}
            </button>
          </Tooltip>
        );
      })}
    </div>
  );
}

/** Single icon button that flips between light and dark (for the collapsed sidebar). */
export function ThemeIconButton() {
  const { theme, toggle } = useTheme();
  return (
    <Tooltip label={strings.theme.toggle} side="right">
      <Button variant="ghost" size="icon" onClick={toggle} aria-label={strings.theme.toggle}>
        {theme === "dark" ? <Sun /> : <Moon />}
      </Button>
    </Tooltip>
  );
}
