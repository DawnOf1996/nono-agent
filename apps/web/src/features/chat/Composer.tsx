import { ArrowUp, Plus, Square } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { Select } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { CommandPalette } from "@/features/chat/CommandPalette";
import { PermissionModeSelect } from "@/features/chat/PermissionModeSelect";
import { FALLBACK_COMMANDS } from "@/lib/commands";
import { formatDuration, formatTokens } from "@/lib/format";
import { useChatStore } from "@/store/chat";

export function Composer() {
  const {
    input,
    setInput,
    send,
    runCommand,
    abort,
    streaming,
    model,
    catalog,
    setModel,
    commands,
    error,
    usage,
  } = useChatStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const slashQuery = input.startsWith("/") ? input.slice(1).split(/\s/)[0] ?? "" : "";
  const paletteOpen = menuOpen || input.startsWith("/");

  useEffect(() => {
    if (!paletteOpen) return;
    function onPointer(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [paletteOpen]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape" && paletteOpen) {
      event.preventDefault();
      setMenuOpen(false);
      if (input.startsWith("/")) setInput("");
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (event.repeat || streaming) return;
      void send();
    }
  }

  return (
    <div className="px-6 pb-3">
      <div className="mx-auto max-w-[52rem]">
        {error ? <div className="mb-2 px-2 text-xs text-danger">{error}</div> : null}
        <div className="rounded-[22px] border border-line bg-bg px-3 pt-3 pb-2 shadow-[0_8px_24px_rgba(15,17,21,0.06)]">
          <Textarea
            rows={2}
            value={input}
            placeholder="描述你想做的事，输入 / 调用指令"
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <div className="mt-2 flex items-center justify-between gap-2">
            <div ref={menuRef} className="relative flex min-w-0 items-center gap-1">
              <CommandPalette
                open={paletteOpen}
                query={slashQuery}
                commands={commands.length ? commands : FALLBACK_COMMANDS}
                onPick={(command) => {
                  setMenuOpen(false);
                  void runCommand(`/${command.name}`);
                }}
              />
              <button
                type="button"
                className="flex size-8 items-center justify-center rounded-full text-muted hover:bg-hover hover:text-ink"
                title="指令"
                disabled={streaming}
                onClick={() => setMenuOpen((value) => !value)}
              >
                <Plus className="size-4" />
              </button>
              <PermissionModeSelect />
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Select
                className="h-8 w-40 border-0 bg-transparent text-[12px] text-muted"
                value={model}
                onChange={(event) => setModel(event.target.value)}
              >
                {!model ? <option value="">选择模型</option> : null}
                {(catalog?.providers ?? []).map((provider) => (
                  <optgroup key={provider.id} label={provider.display_name}>
                    {provider.models.map((item) => (
                      <option key={`${provider.id}:${item.id}`} value={item.id}>
                        {item.name || item.id}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </Select>
              {streaming ? (
                <button
                  type="button"
                  className="flex size-8 items-center justify-center rounded-full bg-accent text-white"
                  onClick={() => void abort()}
                >
                  <Square className="size-3 fill-current" />
                </button>
              ) : (
                <button
                  type="button"
                  className="flex size-8 items-center justify-center rounded-full bg-accent text-white disabled:opacity-30"
                  disabled={!input.trim()}
                  onClick={() => void send()}
                >
                  <ArrowUp className="size-4" />
                </button>
              )}
            </div>
          </div>
        </div>
        {usage ? (
          <div className="mt-1.5 px-2 font-mono text-[11px] text-faint">
            {usage.durationMs ? `${formatDuration(usage.durationMs)} · ` : ""}
            {formatTokens(usage.inputTokens + usage.outputTokens)} tok
            {usage.contextRatio != null
              ? ` · 上下文 ${Math.round(usage.contextRatio * 100)}%`
              : ""}
          </div>
        ) : null}
      </div>
    </div>
  );
}
