import { Cable, ImagePlus, Loader2, Plus, Search, Settings2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { prepareMcpRuntime } from "@/api/client";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

export type ComposerMenuKind = "plus" | "connectors";

type ComposerMenuProps = {
  open: ComposerMenuKind | null;
  onOpenChange: (open: ComposerMenuKind | null) => void;
  disabled?: boolean;
  onAddImages: () => void;
};

export function ComposerMenu({ open, onOpenChange, disabled, onAddImages }: ComposerMenuProps) {
  const servers = useChatStore((state) => state.mcpServers);
  const workspace = useChatStore((state) => state.workspace);
  const setMcpEnabled = useChatStore((state) => state.setMcpEnabled);
  const setSettingsOpen = useChatStore((state) => state.setSettingsOpen);
  const [query, setQuery] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [runtimeHint, setRuntimeHint] = useState<string | null>(null);

  const plusOpen = open === "plus";
  const connectorsOpen = open === "connectors";

  useEffect(() => {
    if (connectorsOpen) return;
    setQuery("");
    setBusyId(null);
    setRuntimeHint(null);
  }, [connectorsOpen]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return servers;
    return servers.filter((item) => item.id.toLowerCase().includes(needle));
  }, [query, servers]);

  const enabledCount = servers.filter((item) => item.enabled).length;
  const hasError = servers.some((item) => item.enabled && item.error);
  const canToggle = Boolean(workspace.trim());

  function togglePanel(kind: ComposerMenuKind) {
    onOpenChange(open === kind ? null : kind);
  }

  function manageConnectors() {
    onOpenChange(null);
    setSettingsOpen(true, "connectors");
  }

  async function toggleConnector(id: string, enabled: boolean, command?: string | null, args?: string[]) {
    if (!canToggle || busyId) return;
    if (!enabled) {
      await setMcpEnabled(id, false);
      return;
    }
    setBusyId(id);
    setRuntimeHint("正在准备运行时…");
    try {
      if (command?.trim()) {
        await prepareMcpRuntime(command, args ?? [], (message) => setRuntimeHint(message));
      }
      await setMcpEnabled(id, true);
      setRuntimeHint(null);
    } catch (error) {
      setRuntimeHint(error instanceof Error ? error.message : "准备运行时失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="flex items-center gap-1">
      <div className="relative">
        <button
          type="button"
          className={cn(
            "flex size-8 items-center justify-center rounded-full text-muted hover:bg-hover hover:text-ink",
            plusOpen && "bg-hover text-ink",
          )}
          title="更多"
          disabled={disabled}
          onClick={() => togglePanel("plus")}
        >
          <Plus className="size-4" />
        </button>
        {plusOpen ? (
          <div className="absolute bottom-[calc(100%+10px)] left-0 z-30 w-[198px] overflow-hidden rounded-2xl border border-line bg-bg py-1 shadow-[0_12px_32px_rgba(15,17,21,0.12)]">
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] text-ink hover:bg-hover"
              onClick={() => {
                onOpenChange(null);
                onAddImages();
              }}
            >
              <ImagePlus className="size-4 text-muted" />
              添加图片
            </button>
          </div>
        ) : null}
      </div>

      <div className="relative">
        <button
          type="button"
          className={cn(
            "relative flex size-8 items-center justify-center rounded-full hover:bg-hover",
            connectorsOpen || enabledCount ? "bg-hover text-ink" : "text-muted hover:text-ink",
          )}
          title={enabledCount ? `连接器 · 已启用 ${enabledCount} 个` : "连接器"}
          disabled={disabled}
          onClick={() => togglePanel("connectors")}
        >
          {busyId ? <Loader2 className="size-4 animate-spin" /> : <Cable className="size-4" />}
          {hasError ? (
            <span className="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-danger" />
          ) : null}
        </button>
        {connectorsOpen ? (
          <div className="absolute bottom-[calc(100%+10px)] left-0 z-30 w-[280px] overflow-hidden rounded-2xl border border-line bg-bg shadow-[0_12px_32px_rgba(15,17,21,0.12)]">
            <div className="border-b border-line px-2.5 py-2">
              <label className="flex h-8 items-center gap-2 rounded-lg bg-card px-2.5 text-muted">
                <Search className="size-3.5 shrink-0" />
                <input
                  value={query}
                  placeholder="搜索连接器"
                  className="h-full w-full bg-transparent text-[13px] text-ink outline-none placeholder:text-faint"
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
            </div>
            <div className="max-h-72 overflow-auto py-1">
              {filtered.length === 0 ? (
                <div className="px-3 py-6 text-center text-[12px] text-faint">
                  {servers.length ? "没有匹配的连接器" : "还没有连接器"}
                </div>
              ) : (
                filtered.map((item) => (
                  <div key={item.id} className="flex items-center gap-2 px-3 py-2">
                    <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-card text-muted">
                      <Cable className="size-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-ink">{item.id}</span>
                      <span className="block truncate text-[11px] text-faint">
                        {busyId === item.id
                          ? runtimeHint || "正在准备运行时…"
                          : item.source === "plugin"
                            ? "插件"
                            : item.transport}
                        {busyId === item.id ? "" : item.running ? " · 已连接" : ""}
                        {busyId === item.id ? "" : item.error ? " · 出错" : ""}
                      </span>
                    </span>
                    {busyId === item.id ? (
                      <Loader2 className="size-4 shrink-0 animate-spin text-muted" />
                    ) : (
                      <Switch
                        checked={item.enabled}
                        disabled={!canToggle || Boolean(busyId)}
                        title={canToggle ? (item.enabled ? "关闭" : "在此工作区启用") : "先选择工作空间"}
                        onCheckedChange={(checked) =>
                          void toggleConnector(item.id, checked, item.command, item.args)
                        }
                      />
                    )}
                  </div>
                ))
              )}
            </div>
            {runtimeHint && !busyId ? (
              <p className="border-t border-line px-3 py-2 text-[11px] leading-4 text-danger">{runtimeHint}</p>
            ) : null}
            <button
              type="button"
              className="flex w-full items-center gap-2 border-t border-line px-3 py-2.5 text-left text-[13px] text-ink hover:bg-hover"
              onClick={manageConnectors}
            >
              <Settings2 className="size-3.5 text-muted" />
              管理连接器
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
