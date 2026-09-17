import { forwardRef, type InputHTMLAttributes, type LabelHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        className={cn(
          "h-9 w-full rounded-lg border border-line bg-bg px-2.5 text-sm text-ink placeholder:text-faint focus:border-line-strong focus:outline-none",
          className,
        )}
        {...props}
      />
    );
  },
);

export function FieldLabel({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn("mb-1 block text-xs text-muted", className)} {...props} />
  );
}
