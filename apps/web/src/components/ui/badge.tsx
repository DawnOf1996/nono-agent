import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md bg-card px-1.5 py-0.5 text-[11px] text-muted",
        className,
      )}
      {...props}
    />
  );
}
