import { useMemo, useState } from "react";

import type { ChatMessage } from "@/store/chat";

export type ChatTurn = {
  id: string;
  userText: string;
  assistantText: string;
};

export function groupChatTurns(messages: ChatMessage[]): ChatTurn[] {
  const turns: ChatTurn[] = [];
  let current: ChatTurn | null = null;
  for (const message of messages) {
    if (message.role === "user") {
      if (current) turns.push(current);
      current = { id: message.id, userText: message.text, assistantText: "" };
      continue;
    }
    if (message.role === "system") continue;
    const text = message.text.trim();
    if (!text) continue;
    if (!current) {
      current = { id: message.id, userText: "", assistantText: text };
    } else if (current.assistantText) {
      current.assistantText = `${current.assistantText}\n${text}`;
    } else {
      current.assistantText = text;
    }
  }
  if (current) turns.push(current);
  return turns;
}

function previewText(text: string, max: number): string {
  const flat = text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]+\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*_~>|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!flat) return "";
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

export function TurnRail({
  turns,
  activeId,
  onJump,
}: {
  turns: ChatTurn[];
  activeId: string | null;
  onJump: (id: string) => void;
}) {
  const [hover, setHover] = useState<{ id: string; top: number } | null>(null);
  const hovered = useMemo(
    () => (hover ? turns.find((item) => item.id === hover.id) : undefined),
    [hover, turns],
  );

  if (turns.length < 2) return null;

  return (
    <div className="pointer-events-none absolute inset-y-8 right-5 z-10 flex w-8 flex-col items-end justify-center">
      <div
        className="pointer-events-auto relative flex max-h-full flex-col items-end gap-px py-1"
        onMouseLeave={() => setHover(null)}
      >
        {turns.map((turn, index) => {
          const active = turn.id === activeId;
          return (
            <button
              key={turn.id}
              type="button"
              aria-label={`第 ${index + 1} 轮`}
              className="group flex h-2 w-5 items-center justify-end"
              onMouseEnter={(event) => {
                const rail = event.currentTarget.parentElement;
                if (!rail) return;
                const railBox = rail.getBoundingClientRect();
                const tickBox = event.currentTarget.getBoundingClientRect();
                setHover({
                  id: turn.id,
                  top: tickBox.top - railBox.top + tickBox.height / 2,
                });
              }}
              onFocus={(event) => {
                const rail = event.currentTarget.parentElement;
                if (!rail) return;
                const railBox = rail.getBoundingClientRect();
                const tickBox = event.currentTarget.getBoundingClientRect();
                setHover({
                  id: turn.id,
                  top: tickBox.top - railBox.top + tickBox.height / 2,
                });
              }}
              onClick={() => onJump(turn.id)}
            >
              <span
                className={`block h-[3px] rounded-full transition-[width,background-color] duration-150 ${
                  active ? "w-4 bg-ink" : "w-2.5 bg-line-strong group-hover:w-3.5 group-hover:bg-muted"
                }`}
              />
            </button>
          );
        })}
        {hovered && hover ? (
          <div
            className="pointer-events-none absolute right-7 w-56 -translate-y-1/2 rounded-xl border border-line bg-bg px-3 py-2.5 shadow-[0_12px_32px_rgba(15,17,21,0.12)]"
            style={{ top: Math.max(28, hover.top) }}
          >
            <div className="text-[12px] leading-5 font-medium text-ink">
              {previewText(hovered.userText, 42) || `第 ${turns.findIndex((item) => item.id === hovered.id) + 1} 轮`}
            </div>
            {hovered.assistantText ? (
              <div className="mt-0.5 line-clamp-3 text-[11px] leading-4 text-faint">
                {previewText(hovered.assistantText, 96)}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
