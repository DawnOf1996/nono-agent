import type { PermissionMode } from "@nono/protocol";

const MODE_KEY = "nono.draft.permission_mode";

export const PERMISSION_MODES: PermissionMode[] = [
  "default",
  "accept_edits",
  "plan",
  "bypass",
];

export const PERMISSION_MODE_META: Record<
  PermissionMode,
  { label: string; hint: string; danger?: boolean }
> = {
  default: {
    label: "每次确认",
    hint: "读取自动允许，写入和执行需批准",
  },
  accept_edits: {
    label: "自动编辑",
    hint: "工作区内改文件自动允许，其它写操作仍需确认",
  },
  plan: {
    label: "计划模式",
    hint: "只读，禁止任何写副作用",
  },
  bypass: {
    label: "全部允许",
    hint: "跳过确认；显式拒绝规则仍生效",
    danger: true,
  },
};

export function isPermissionMode(value: string | null | undefined): value is PermissionMode {
  return PERMISSION_MODES.includes(value as PermissionMode);
}

export function normalizePermissionMode(value: string | null | undefined): PermissionMode {
  return isPermissionMode(value) ? value : "default";
}

export function loadDraftPermissionMode(): PermissionMode {
  try {
    return normalizePermissionMode(localStorage.getItem(MODE_KEY));
  } catch {
    return "default";
  }
}

export function saveDraftPermissionMode(mode: PermissionMode): void {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    // private mode / blocked storage
  }
}
