import { cn } from "@/lib/utils";

export function DiffStat({
  added,
  deleted,
  binary,
  oversized,
  className,
}: {
  added: number;
  deleted: number;
  binary?: boolean;
  oversized?: boolean;
  className?: string;
}) {
  if (binary || oversized) {
    return (
      <span className={cn("shrink-0 text-[11px] text-faint", className)}>{binary ? "二进制" : "过大"}</span>
    );
  }
  return (
    <span className={cn("inline-flex shrink-0 gap-1.5 font-mono text-[11px] tabular-nums", className)}>
      <span className="text-[#2f8a4c]">+{added}</span>
      <span className="text-[#c4403f]">-{deleted}</span>
    </span>
  );
}

const STATUS_TONE: Record<string, string> = {
  M: "text-[#b7791f]",
  A: "text-[#2f8a4c]",
  U: "text-[#2f8a4c]",
  "?": "text-[#2f8a4c]",
  D: "text-[#c4403f]",
  R: "text-[#3366cc]",
  C: "text-[#3366cc]",
  T: "text-[#b7791f]",
  "!": "text-[#c4403f]",
};

/** git 风格的单字母状态徽标。 */
export function StatusBadge({ letter, title }: { letter: string; title?: string }) {
  const shown = letter === "?" ? "U" : letter;
  return (
    <span
      title={title}
      className={cn("w-3.5 shrink-0 text-center font-mono text-[11px] font-semibold", STATUS_TONE[letter] ?? "text-muted")}
    >
      {shown}
    </span>
  );
}

export function statusLetter(status: string): string {
  switch (status) {
    case "added":
      return "A";
    case "deleted":
      return "D";
    case "renamed":
      return "R";
    default:
      return "M";
  }
}
