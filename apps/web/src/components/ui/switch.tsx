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
          // 拨块要同时压得住两种底色：打开时压在 accent 上，没打开时压在 line-strong 上，
          // 所以两种状态各取一个"和当前底色对调"的色，写死 bg-white 在深色下会整个消失。
          "absolute top-0.5 size-4 rounded-full transition-[left,background-color]",
          checked ? "left-[18px] bg-on-accent" : "left-0.5 bg-bg",
        )}
      />
    </button>
  );
}
