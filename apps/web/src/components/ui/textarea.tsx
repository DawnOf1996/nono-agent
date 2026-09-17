import type { TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full resize-none bg-transparent text-[15px] leading-6 text-ink placeholder:text-faint focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}
