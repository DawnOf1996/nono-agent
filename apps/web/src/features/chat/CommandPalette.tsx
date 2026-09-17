import type { SlashCommandInfo } from "@nono/protocol";

import { commandLabel, filterCommands } from "@/lib/commands";

type CommandPaletteProps = {
  open: boolean;
  query: string;
  commands: SlashCommandInfo[];
  onPick: (command: SlashCommandInfo) => void;
};

export function CommandPalette({ open, query, commands, onPick }: CommandPaletteProps) {
  const items = filterCommands(commands, query);

  if (!open) return null;

  return (
    <div className="absolute bottom-[calc(100%+10px)] left-0 z-20 w-[min(28rem,calc(100vw-3rem))] overflow-hidden rounded-2xl border border-line bg-bg shadow-[0_12px_32px_rgba(15,17,21,0.12)]">
      <div className="border-b border-line px-3 py-2 text-[11px] text-faint">指令</div>
      <div className="max-h-72 overflow-auto p-1">
        {items.length === 0 ? (
          <div className="px-3 py-4 text-[12px] text-faint">没有匹配的指令</div>
        ) : (
          items.map((command) => (
            <button
              key={command.name}
              type="button"
              className="flex w-full items-baseline gap-2 rounded-xl px-3 py-2 text-left hover:bg-hover"
              onClick={() => onPick(command)}
            >
              <span className="shrink-0 font-mono text-[13px] text-ink">/{command.name}</span>
              <span className="truncate text-[12px] text-muted">{commandLabel(command)}</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
