import { Loader2 } from "lucide-react";

export function PreviewLoading({ label = "正在读取" }: { label?: string }) {
  return (
    <div className="flex h-full min-h-24 items-center justify-center gap-2 text-[13px] text-faint">
      <Loader2 className="size-4 animate-spin" />
      {label}
    </div>
  );
}
