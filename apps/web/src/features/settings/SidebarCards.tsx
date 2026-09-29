import { Settings } from "lucide-react";
import { useState, useSyncExternalStore } from "react";

import { Switch } from "@/components/ui/switch";
import {
  getSidebarPrefs,
  subscribeSidebarPrefs,
  updateSidebarPrefs,
  type SidebarPrefs,
} from "@/features/settings/sidebar-prefs";

const CARDS: Array<{
  id: keyof Pick<SidebarPrefs, "files" | "changes" | "tasks" | "terminal" | "browser" | "sidechat">;
  title: string;
  detail: string;
  settings?: "terminal" | "browser" | "files";
}> = [
  { id: "files", title: "文件", detail: "资源管理器和文件预览", settings: "files" },
  { id: "changes", title: "变更", detail: "Git 状态和本轮文件" },
  { id: "tasks", title: "任务", detail: "子代理和正在执行的命令" },
  { id: "terminal", title: "终端", detail: "工作区里的 shell", settings: "terminal" },
  { id: "browser", title: "浏览器", detail: "内嵌网页标签", settings: "browser" },
  { id: "sidechat", title: "侧边对话", detail: "继承主会话的独立线程" },
];

const PREVIEWERS: Array<{ id: keyof SidebarPrefs["previewers"]; title: string; detail: string; settings?: "html" }> = [
  { id: "markdown", title: "Markdown", detail: "渲染标题、列表和图表" },
  { id: "html", title: "HTML", detail: "在沙箱里打开网页", settings: "html" },
  { id: "image", title: "图片", detail: "查看常见图片格式" },
  { id: "pdf", title: "PDF", detail: "翻页阅读文档" },
  { id: "code", title: "代码", detail: "语法高亮显示源码" },
  { id: "sheet", title: "表格", detail: "CSV 和 Excel" },
  { id: "docx", title: "Word", detail: "阅读 Word 文档" },
  { id: "pptx", title: "PowerPoint", detail: "翻页查看幻灯片" },
];

export function SidebarCards() {
  const prefs = useSyncExternalStore(subscribeSidebarPrefs, getSidebarPrefs, getSidebarPrefs);
  const [editing, setEditing] = useState<"terminal" | "browser" | "files" | "html" | null>(null);
  return (
    <div className="mt-5">
      <h4 className="text-[13px] font-medium">侧边栏</h4>
      <p className="mt-1 text-[12px] text-muted">每种标签可以单独关掉。功能设置会记在这台电脑上。</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {CARDS.map((card) => (
          <div
            key={card.id}
            className={`rounded-2xl border px-3 py-3 ${prefs[card.id] ? "border-ink/20 bg-sidebar" : "border-line"}`}
          >
            <div className="flex items-center justify-between gap-2">
              <p className="min-w-0 text-[13px] font-medium">{card.title}</p>
              <Switch
                checked={prefs[card.id]}
                title={prefs[card.id] ? `关闭${card.title}` : `启用${card.title}`}
                onCheckedChange={(checked) => updateSidebarPrefs({ [card.id]: checked })}
              />
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-2">
              <p className="min-w-0 text-[12px] text-muted">{card.detail}</p>
              {card.settings ? (
                <button
                  type="button"
                  title="功能设置"
                  aria-label="功能设置"
                  className="grid h-5 w-9 shrink-0 place-items-center rounded-md text-muted hover:bg-ink/5 hover:text-ink"
                  onClick={() => setEditing(card.settings ?? null)}
                >
                  <Settings className="size-3.5" />
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
      <h4 className="mt-5 text-[13px] font-medium">预览器</h4>
      <p className="mt-1 text-[12px] text-muted">关掉某个预览器后，这种文件会改用别的方式打开。</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {PREVIEWERS.map((card) => (
          <div
            key={card.id}
            className={`rounded-2xl border px-3 py-3 ${prefs.previewers[card.id] ? "border-ink/20 bg-sidebar" : "border-line"}`}
          >
            <div className="flex items-center justify-between gap-2">
              <p className="min-w-0 text-[13px] font-medium">{card.title}</p>
              <Switch
                checked={prefs.previewers[card.id]}
                title={prefs.previewers[card.id] ? `关闭${card.title}` : `启用${card.title}`}
                onCheckedChange={(checked) =>
                  updateSidebarPrefs({ previewers: { ...prefs.previewers, [card.id]: checked } })
                }
              />
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-2">
              <p className="min-w-0 text-[12px] text-muted">{card.detail}</p>
              {card.settings ? (
                <button
                  type="button"
                  title="功能设置"
                  aria-label="功能设置"
                  className="grid h-5 w-9 shrink-0 place-items-center rounded-md text-muted hover:bg-ink/5 hover:text-ink"
                  onClick={() => setEditing("html")}
                >
                  <Settings className="size-3.5" />
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
      {editing ? <CardSettings kind={editing} prefs={prefs} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function CardSettings({
  kind,
  prefs,
  onClose,
}: {
  kind: "terminal" | "browser" | "files" | "html";
  prefs: SidebarPrefs;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6" onClick={onClose}>
      <div className="w-full max-w-sm rounded-2xl border border-line bg-bg p-4 shadow-lg" onClick={(event) => event.stopPropagation()}>
        <h4 className="text-[14px] font-medium">
          {kind === "terminal" ? "终端" : kind === "browser" ? "浏览器" : kind === "html" ? "HTML" : "文件"}
        </h4>
        {kind === "terminal" ? (
          <label className="mt-3 block text-[13px]">
            字号
            <input
              type="number"
              min={10}
              max={22}
              value={prefs.terminalFontSize}
              className="mt-1 h-8 w-full rounded-lg border border-line px-2"
              onChange={(event) => updateSidebarPrefs({ terminalFontSize: Number(event.target.value) || 13 })}
            />
          </label>
        ) : null}
        {kind === "browser" ? (
          <label className="mt-3 block text-[13px]">
            新标签的起始地址
            <input
              value={prefs.browserHome}
              placeholder="https://"
              className="mt-1 h-8 w-full rounded-lg border border-line px-2"
              onChange={(event) => updateSidebarPrefs({ browserHome: event.target.value })}
            />
          </label>
        ) : null}
        {kind === "html" ? (
          <div className="mt-3 flex flex-col gap-3 text-[13px]">
            <label className="flex items-center justify-between gap-3">
              不使用沙箱
              <Switch checked={prefs.htmlNoSandbox} onCheckedChange={(checked) => updateSidebarPrefs({ htmlNoSandbox: checked })} />
            </label>
            <label className="flex items-center justify-between gap-3">
              允许同源脚本
              <Switch checked={prefs.htmlUnsafe} onCheckedChange={(checked) => updateSidebarPrefs({ htmlUnsafe: checked })} />
            </label>
          </div>
        ) : null}
        {kind === "files" ? (
          <div className="mt-3 flex flex-col gap-3 text-[13px]">
            <label className="flex items-center justify-between gap-3">
              显示隐藏文件
              <Switch checked={prefs.showHiddenFiles} onCheckedChange={(checked) => updateSidebarPrefs({ showHiddenFiles: checked })} />
            </label>
            <label className="flex items-center justify-between gap-3">
              打开对话时展开文件树
              <Switch
                checked={prefs.defaultOpenFileTree}
                onCheckedChange={(checked) => updateSidebarPrefs({ defaultOpenFileTree: checked })}
              />
            </label>
            <label className="block">
              资源管理器
              <select
                className="mt-1 h-8 w-full rounded-lg border border-line bg-bg px-2"
                value={prefs.explorerMode}
                onChange={(event) =>
                  updateSidebarPrefs({ explorerMode: event.target.value === "merged" ? "merged" : "split" })
                }
              >
                <option value="split">独立文件树</option>
                <option value="merged">嵌在预览里</option>
              </select>
            </label>
          </div>
        ) : null}
        <button type="button" className="mt-4 h-8 rounded-lg bg-ink px-3 text-[13px] text-white" onClick={onClose}>
          完成
        </button>
      </div>
    </div>
  );
}
