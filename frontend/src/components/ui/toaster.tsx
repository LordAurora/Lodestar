import { Toaster as Sonner } from "sonner";
import { useTheme } from "@/lib/theme";

/** Toast notifications (shadcn/ui uses Sonner for its Toast component). */
export function Toaster() {
  const { theme } = useTheme();
  return (
    <Sonner
      theme={theme}
      position="bottom-right"
      toastOptions={{
        classNames: {
          toast:
            "!rounded-lg !border !border-border !bg-background !text-foreground !shadow-sm !font-sans !text-sm",
          description: "!text-muted",
        },
      }}
    />
  );
}

export { toast } from "sonner";
