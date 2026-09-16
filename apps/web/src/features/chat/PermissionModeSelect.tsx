import { ChevronDown, ListChecks, Pencil, Shield, ShieldAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { PermissionMode } from "@nono/protocol";

import { cn } from "@/lib/utils";
import { PERMISSION_MODE_META, PERMISSION_MODES } from "@/lib/permissions";
import { useChatStore } from "@/store/chat";

const ICONS: Record<PermissionMode, typeof Shield> = {
  default: Shield,
  accept_edits: Pencil,
  plan: ListChecks,
  bypass: ShieldAlert,
};

export function PermissionModeSelect() {
  const permissionMode = useChatStore((state) => state.permissionMode);
  const setPermissionMode = useChatStore((state) => state.setPermissionMode);
  const streaming = useChatStore((state) => state.streaming);
  const [open, setOpen] = useState(false);
  const [confirmBypass, setConfirmBypass] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const current = PERMISSION_MODE_META[permissionMode];
  const CurrentIcon = ICONS[permissionMode];

  useEffect(() => {
    if (!open) {
      setConfirmBypass(false);
      return;
    }
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

  async function choose(mode: PermissionMode) {
    if (mode === "bypass" && permissionMode !== "bypass" && !confirmBypass) {
      setConfirmBypass(true);
      return;
    }
    setOpen(false);
    setConfirmBypass(false);
    await setPermissionMode(mode);
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        className="flex min-w-0 items-center gap-1 rounded-full px-2 py-1 text-[12px] text-muted hover:bg-hover"
        title={current.hint}
        disabled={streaming}
        onClick={() => setOpen((value) => !value)}
      >
        <CurrentIcon className={cn("size-3.5", current.danger && "text-danger")} />
        <span className="truncate">{current.label}</span>
        <ChevronDown className={cn("size-3 shrink-0", open && "rotate-180")} />
      </button>
      {open ? (
        <div className="absolute bottom-[calc(100%+6px)] left-0 z-20 w-[240px] rounded-xl border border-line bg-bg p-1 shadow-[0_12px_32px_rgba(15,17,21,0.12)]">
          {PERMISSION_MODES.map((mode) => {
            const meta = PERMISSION_MODE_META[mode];
            const Icon = ICONS[mode];
            const selected = mode === permissionMode;
            return (
              <button
                key={mode}
                type="button"
                className={cn(
                  "flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-hover",
                  selected && "bg-active",
                )}
                onClick={() => void choose(mode)}
              >
                <Icon className={cn("mt-0.5 size-3.5 shrink-0", meta.danger ? "text-danger" : "text-muted")} />
                <span className="min-w-0">
                  <span className={cn("block text-[12px]", meta.danger ? "text-danger" : "text-ink")}>
                    {meta.label}
                  </span>
                  <span className="mt-0.5 block text-[11px] leading-4 text-faint">{meta.hint}</span>
                </span>
              </button>
            );
          })}
          {confirmBypass ? (
            <div className="mt-1 rounded-lg bg-danger/5 px-2.5 py-2">
              <div className="text-[11px] leading-4 text-danger">全部允许会跳过工具确认，确定开启？</div>
              <div className="mt-2 flex justify-end gap-1.5">
                <button
                  type="button"
                  className="rounded-md px-2 py-1 text-[11px] text-muted hover:bg-hover"
                  onClick={() => setConfirmBypass(false)}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="rounded-md bg-danger px-2 py-1 text-[11px] text-white"
                  onClick={() => void choose("bypass")}
                >
                  开启
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
