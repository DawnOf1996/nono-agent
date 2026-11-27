import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

type CheckboxProps = {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  label: string;
  hint?: string;
  className?: string;
  id?: string;
};

/**
 * 复选框 —— 设置页里「多选但各自独立」的字段（如模型的输入类型）。
 *
 * 配色沿用 Switch 的那套约定：选中底色用 accent（ink），勾号用 on-accent
 * 单独一个 token —— 深色模式下 accent 会翻成浅色，写死白字会糊掉。
 */
export function Checkbox({
  checked,
  onCheckedChange,
  disabled,
  label,
  hint,
  className,
  id,
}: CheckboxProps) {
  const boxId = id ?? `checkbox-${label}`;
  return (
    <label
      htmlFor={boxId}
      className={cn(
        "flex min-w-0 items-center gap-1.5 text-[12px] leading-5 text-ink",
        disabled && "cursor-not-allowed opacity-40",
        !disabled && "cursor-pointer",
        className,
      )}
    >
      <input
        id={boxId}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onCheckedChange(event.target.checked)}
        className="peer sr-only"
      />
      <span
        aria-hidden
        className={cn(
          "flex size-4 shrink-0 items-center justify-center rounded-[5px] border transition-colors",
          "peer-focus-visible:ring-2 peer-focus-visible:ring-line-strong peer-focus-visible:ring-offset-1",
          checked ? "border-ink bg-ink" : "border-line-strong bg-card",
        )}
      >
        {checked ? <Check className="size-3 text-on-accent" strokeWidth={3} /> : null}
      </span>
      <span className="min-w-0 truncate">{label}</span>
      {hint ? <span className="truncate text-[11px] text-faint">{hint}</span> : null}
    </label>
  );
}
