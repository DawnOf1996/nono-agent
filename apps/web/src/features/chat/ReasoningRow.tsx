import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

export function ReasoningRow({ text, running }: { text: string; running: boolean }) {
  const [expanded, setExpanded] = useState(running);

  useEffect(() => {
    setExpanded(running);
  }, [running]);

  if (!text.trim() && !running) return null;

  return (
    <div className="mb-3 text-[13px] leading-5 text-muted" data-state={running ? "running" : "ok"}>
      <button
        type="button"
        className="flex w-full min-w-0 items-center gap-1.5 text-left hover:text-ink"
        onClick={() => setExpanded((value) => !value)}
      >
        <ChevronRight className={`size-3.5 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`} />
        <span>思考</span>
        {running && expanded ? <span className="text-faint">进行中</span> : null}
      </button>
      {expanded ? (
        <div className="mt-1 whitespace-pre-wrap break-words pl-[22px] text-muted">{text || "…"}</div>
      ) : null}
    </div>
  );
}
