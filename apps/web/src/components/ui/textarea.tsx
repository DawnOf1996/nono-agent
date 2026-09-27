import { forwardRef, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return (
      <textarea
        ref={ref}
        className={cn(
          "w-full resize-none bg-transparent text-[15px] leading-6 text-ink placeholder:text-faint focus:outline-none",
          className,
        )}
        {...props}
      />
    );
  },
);
