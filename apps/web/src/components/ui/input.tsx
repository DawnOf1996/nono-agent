import type { InputHTMLAttributes, LabelHTMLAttributes, SelectHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full border border-line bg-ink px-2.5 text-sm text-paper placeholder:text-muted focus:border-copper/70 focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-9 w-full border border-line bg-ink px-2 text-sm text-paper focus:border-copper/70 focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}

export function FieldLabel({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn(
        "mb-1 block font-mono text-[10px] uppercase tracking-[0.16em] text-muted",
        className,
      )}
      {...props}
    />
  );
}
