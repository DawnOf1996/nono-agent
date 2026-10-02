import { forwardRef, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return (
      <textarea
        ref={ref}
        className={cn(
          // 输入框跟会话正文同一个字号（默认 14px），写在已经排好的对话里才不会突然跳大一号。
          "w-full resize-none bg-transparent text-[length:var(--content-font-size,14px)] leading-6 text-ink placeholder:text-faint focus:outline-none",
          className,
        )}
        {...props}
      />
    );
  },
);
