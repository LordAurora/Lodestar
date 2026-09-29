import * as SliderPrimitive from "@radix-ui/react-slider";

export function Slider({
  value,
  onValueChange,
  onValueCommit,
  min,
  max,
  step,
  label,
}: {
  value: number;
  onValueChange: (v: number) => void;
  onValueCommit?: (v: number) => void;
  min: number;
  max: number;
  step: number;
  label: string;
}) {
  return (
    <SliderPrimitive.Root
      className="relative flex h-5 w-full touch-none items-center select-none"
      value={[value]}
      min={min}
      max={max}
      step={step}
      onValueChange={([v]) => onValueChange(v)}
      onValueCommit={([v]) => onValueCommit?.(v)}
    >
      <SliderPrimitive.Track className="relative h-1.5 grow rounded-full bg-surface-2">
        <SliderPrimitive.Range className="absolute h-full rounded-full bg-accent" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb
        aria-label={label}
        className="block size-4 rounded-full border-2 border-accent bg-background focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-foreground/15"
      />
    </SliderPrimitive.Root>
  );
}
