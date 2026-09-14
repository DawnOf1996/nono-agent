import type { TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full resize-none bg-transparent text-sm text-paper placeholder:text-muted focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}
