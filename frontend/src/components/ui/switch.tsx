import * as SwitchPrimitive from "@radix-ui/react-switch";

export function Switch({
  checked,
  onCheckedChange,
  id,
}: {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  id?: string;
}) {
  return (
    <SwitchPrimitive.Root
      id={id}
      checked={checked}
      onCheckedChange={onCheckedChange}
      className="inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full bg-surface-2 border border-border transition-colors data-[state=checked]:border-accent data-[state=checked]:bg-accent"
    >
      <SwitchPrimitive.Thumb className="block size-4 translate-x-0.5 rounded-full bg-background transition-transform data-[state=checked]:translate-x-[17px] data-[state=checked]:bg-white" />
    </SwitchPrimitive.Root>
  );
}
