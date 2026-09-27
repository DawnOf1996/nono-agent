/** 侧边栏每种标签的开关，以及它们的二级设置。写在本机 localStorage。 */

export type SidebarPrefs = {
  files: boolean;
  changes: boolean;
  tasks: boolean;
  terminal: boolean;
  browser: boolean;
  sidechat: boolean;
  terminalFontSize: number;
  browserHome: string;
  showHiddenFiles: boolean;
  /** split：文件页只显示树，打开文件进新标签。merged：树嵌在预览左侧，点击在当前页切换。 */
  explorerMode: "split" | "merged";
  /** 关掉的预览器不再认领对应文件，会落到下一个预览或纯文本。 */
  previewers: {
    markdown: boolean;
    html: boolean;
    image: boolean;
    pdf: boolean;
    code: boolean;
    sheet: boolean;
    docx: boolean;
    pptx: boolean;
  };
  /** 去掉 HTML iframe 的 sandbox。 */
  htmlNoSandbox: boolean;
  /** HTML 预览允许同源脚本。 */
  htmlUnsafe: boolean;
};

export const SIDEBAR_PREFS_DEFAULTS: SidebarPrefs = {
  files: true,
  changes: true,
  tasks: true,
  terminal: true,
  browser: true,
  sidechat: true,
  terminalFontSize: 13,
  browserHome: "",
  showHiddenFiles: true,
  explorerMode: "split",
  previewers: {
    markdown: true,
    html: true,
    image: true,
    pdf: true,
    code: true,
    sheet: true,
    docx: true,
    pptx: true,
  },
  htmlNoSandbox: false,
  htmlUnsafe: false,
};

const KEY = "nono.sidebar.cards";

let current = readPrefs();
const listeners = new Set<() => void>();

function readPrefs(): SidebarPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...SIDEBAR_PREFS_DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<SidebarPrefs>;
    return {
      ...SIDEBAR_PREFS_DEFAULTS,
      ...parsed,
      previewers: { ...SIDEBAR_PREFS_DEFAULTS.previewers, ...parsed.previewers },
      explorerMode: parsed.explorerMode === "merged" ? "merged" : SIDEBAR_PREFS_DEFAULTS.explorerMode,
    };
  } catch {
    return { ...SIDEBAR_PREFS_DEFAULTS };
  }
}

export function getSidebarPrefs(): SidebarPrefs {
  return current;
}

export function subscribeSidebarPrefs(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function updateSidebarPrefs(patch: Partial<SidebarPrefs>): void {
  current = { ...current, ...patch };
  try {
    localStorage.setItem(KEY, JSON.stringify(current));
  } catch {
    // private mode / blocked storage
  }
  for (const listener of listeners) listener();
}
