import { Box, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

export function ModelSelect() {
  const model = useChatStore((state) => state.model);
  const catalog = useChatStore((state) => state.catalog);
  const setModel = useChatStore((state) => state.setModel);
  const streaming = useChatStore((state) => state.streaming);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const providers = catalog?.providers ?? [];
  const current = providers
    .flatMap((provider) => provider.models.map((item) => ({ provider, item })))
    .find((entry) => entry.item.id === model);
  const label = current?.item.name || current?.item.id || model || "选择模型";

  useEffect(() => {
    if (!open) return;
    function onPointer(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function choose(id: string) {
    setOpen(false);
    await setModel(id);
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        className="flex min-w-0 max-w-[10rem] items-center gap-1 rounded-full px-2 py-1 text-[12px] text-muted hover:bg-hover"
        title={label}
        disabled={streaming}
        onClick={() => setOpen((value) => !value)}
      >
        <Box className="size-3.5 shrink-0" />
        <span className="truncate">{label}</span>
        <ChevronDown className={cn("size-3 shrink-0", open && "rotate-180")} />
      </button>
      {open ? (
        <div className="absolute bottom-[calc(100%+6px)] right-0 z-20 max-h-[280px] w-[240px] overflow-auto rounded-xl border border-line bg-bg p-1 shadow-[0_12px_32px_rgba(15,17,21,0.12)]">
          {providers.length === 0 ? (
            <div className="px-2.5 py-2 text-[12px] text-muted">还没有可用模型</div>
          ) : (
            providers.map((provider) => (
              <div key={provider.id}>
                <div className="px-2.5 pt-2 pb-1 text-[11px] text-faint">
                  {provider.display_name}
                  {provider.has_key ? "" : " · 缺密钥"}
                </div>
                {provider.models.map((item) => {
                  const selected = item.id === model;
                  const name = item.name || item.id;
                  return (
                    <button
                      key={`${provider.id}:${item.id}`}
                      type="button"
                      className={cn(
                        "flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-hover",
                        selected && "bg-active",
                      )}
                      onClick={() => void choose(item.id)}
                    >
                      <Box className="mt-0.5 size-3.5 shrink-0 text-muted" />
                      <span className="min-w-0">
                        <span className="block truncate text-[12px] text-ink">{name}</span>
                        <span className="mt-0.5 block truncate text-[11px] leading-4 text-faint">
                          {item.id}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}
