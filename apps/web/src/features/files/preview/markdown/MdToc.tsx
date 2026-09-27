import { ListTree } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type TocEntry = { level: number; text: string; el: HTMLElement };

const MIN_HEADINGS = 3;

/** 挂在预览滚动容器里。标题不少于 3 个时，右上角出现可跳转的大纲。 */
export function MdToc() {
  const barRef = useRef<HTMLDivElement>(null);
  const [entries, setEntries] = useState<TocEntry[]>([]);
  const [open, setOpen] = useState(false);
  const signature = useRef("");

  useEffect(() => {
    const container = barRef.current?.parentElement;
    if (!container) return;
    const scan = () => {
      const found: TocEntry[] = [];
      for (const el of container.querySelectorAll<HTMLElement>("h1, h2, h3, h4, h5, h6")) {
        const text = (el.textContent ?? "").trim();
        if (!text || el.closest(".md-toc")) continue;
        found.push({ level: Number(el.tagName.charAt(1)), text, el });
      }
      const next = found.map((entry) => `${entry.level}:${entry.text}`).join("\n");
      if (next === signature.current) return;
      signature.current = next;
      setEntries(found);
    };
    scan();
    const observer = new MutationObserver(scan);
    observer.observe(container, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div ref={barRef} className="md-toc pointer-events-none sticky top-2 z-10 -mb-8 flex h-8 justify-end">
      {entries.length >= MIN_HEADINGS ? (
        <div className="pointer-events-auto relative">
          <button
            type="button"
            className="flex size-8 items-center justify-center rounded-lg border border-line bg-bg text-muted shadow-sm hover:text-ink"
            title="目录"
            aria-label="目录"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            <ListTree className="size-4" />
          </button>
          {open ? (
            <div className="absolute right-0 mt-1 max-h-80 w-64 overflow-auto rounded-lg border border-line bg-bg py-1 shadow-lg">
              {entries.map((entry, index) => (
                <button
                  key={`${entry.text}-${index}`}
                  type="button"
                  className="block w-full truncate px-3 py-1 text-left text-[12px] text-ink/80 hover:bg-hover"
                  style={{ paddingLeft: 8 + (entry.level - 1) * 12 }}
                  title={entry.text}
                  onClick={() => {
                    let node: HTMLElement | null = entry.el;
                    while (node) {
                      if (node instanceof HTMLDetailsElement) node.open = true;
                      node = node.parentElement;
                    }
                    entry.el.scrollIntoView({ behavior: "smooth", block: "start" });
                    setOpen(false);
                  }}
                >
                  {entry.text}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
