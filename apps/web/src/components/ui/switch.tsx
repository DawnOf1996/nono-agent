import { cn } from "@/lib/utils";

type SwitchProps = {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  title?: string;
};

export function Switch({ checked, onCheckedChange, disabled, title }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      title={title}
      disabled={disabled}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-40",
        checked ? "bg-ink" : "bg-line-strong",
      )}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onCheckedChange(!checked);
      }}
    >
      <span
        className={cn(
          "absolute top-0.5 size-4 rounded-full bg-white transition-[left]",
          checked ? "left-[18px]" : "left-0.5",
        )}
      />
    </button>
  );
}
