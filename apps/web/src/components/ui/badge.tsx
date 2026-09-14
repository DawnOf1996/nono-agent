import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border border-line px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted",
        className,
      )}
      {...props}
    />
  );
}
