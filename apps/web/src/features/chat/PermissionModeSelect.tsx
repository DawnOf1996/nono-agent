import { ChevronDown, ListChecks, Pencil, Shield, ShieldAlert } from "lucide-react";
import { useState } from "react";
import type { PermissionMode } from "@nono/protocol";

import { Dropdown, DropdownItem } from "@/components/ui/dropdown";
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
  const current = PERMISSION_MODE_META[permissionMode];
  const CurrentIcon = ICONS[permissionMode];

  function close() {
    setOpen(false);
    setConfirmBypass(false);
  }

  async function choose(mode: PermissionMode) {
    if (mode === "bypass" && permissionMode !== "bypass" && !confirmBypass) {
      setConfirmBypass(true);
      return;
    }
    close();
    await setPermissionMode(mode);
  }

  return (
    <Dropdown
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setConfirmBypass(false);
      }}
      placement="top"
      panelClassName="w-[240px]"
      trigger={
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
      }
    >
      {PERMISSION_MODES.map((mode) => {
        const meta = PERMISSION_MODE_META[mode];
        const Icon = ICONS[mode];
        const selected = mode === permissionMode;
        return (
          <DropdownItem key={mode} selected={selected} onClick={() => void choose(mode)}>
            <Icon className={cn("mt-0.5 size-3.5 shrink-0", meta.danger ? "text-danger" : "text-muted")} />
            <span className="min-w-0">
              <span className={cn("block text-[12px]", meta.danger ? "text-danger" : "text-ink")}>
                {meta.label}
              </span>
              <span className="mt-0.5 block text-[11px] leading-4 text-faint">{meta.hint}</span>
            </span>
          </DropdownItem>
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
              className="rounded-md bg-danger px-2 py-1 text-[11px] text-on-danger"
              onClick={() => void choose("bypass")}
            >
              开启
            </button>
          </div>
        </div>
      ) : null}
    </Dropdown>
  );
}
