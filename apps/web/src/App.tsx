import { useEffect } from "react";

import { usePanelStore } from "@/features/files/panel-store";
import { PermissionDialog } from "@/features/permissions/PermissionDialog";
import { NewTaskDialog } from "@/features/session/NewTaskDialog";
import { SettingsPanel } from "@/features/settings/SettingsPanel";
import { Workbench } from "@/features/shell/Workbench";
import { useChatStore } from "@/store/chat";
import { Toaster } from "@/components/ui/toast";
import { httpUrl } from "@/lib/url";
import { useGlobalLinkRouter } from "@/lib/use-link-router";

export function App() {
  const refresh = useChatStore((state) => state.refresh);
  const watchControl = useChatStore((state) => state.watchControl);
  const unwatchControl = useChatStore((state) => state.unwatchControl);
  const watchSessionStats = useChatStore((state) => state.watchSessionStats);
  const unwatchSessionStats = useChatStore((state) => state.unwatchSessionStats);
  const sessionId = useChatStore((state) => state.sessionId);
  const openBrowser = usePanelStore((state) => state.openBrowser);
  // 根节点上一层点击代理：正文里的网址开成侧边栏浏览器标签，文件路径开成侧边栏
  // 文件查看器。桌面版装在 <webview> 里，链接一旦自己开窗口就会弹出原生窗体。
  useGlobalLinkRouter();

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 健康与待授权请求都走这一条常驻流：后台会话的授权请求也得被收上来，
  // 所以跟有没有会话在跑无关，整条应用存活期都开着。
  useEffect(() => {
    watchControl();
    return () => unwatchControl();
  }, [watchControl, unwatchControl]);

  // 底部统计只订阅**正在浏览**的那个会话：服务端在引擎每落一步事件时推一帧，
  // 这里既不轮询也不订阅别的会话。切换会话时关掉旧流再开新流。
  useEffect(() => {
    if (!sessionId) return;
    watchSessionStats(sessionId);
    return () => unwatchSessionStats();
  }, [sessionId, watchSessionStats, unwatchSessionStats]);

  // 访客页里的 target=_blank / window.open 改开成应用内的浏览器标签。拦截发生在主进程
  // 对 guest 的 WebContents 上（webview 的 new-window 事件在 Electron 37 已经不触发了），
  // 这里只把收到的地址开成新标签。
  //
  // 订阅只能有一份，放在 App 而不是每个 BrowserPane：否则每开一个浏览器标签就多挂一个
  // 监听器，点一次链接新增的标签数会 1→2→4 翻倍。
  useEffect(() => {
    const subscribe = typeof window !== "undefined" ? window.nonoDesktop?.onNewTab : undefined;
    if (!subscribe) return;
    return subscribe((url) => {
      const safe = httpUrl(url);
      if (safe) openBrowser(safe);
    });
  }, [openBrowser]);

  return (
    <div className="flex h-full min-h-0 bg-bg">
      <Workbench />
      <PermissionDialog />
      <NewTaskDialog />
      <SettingsPanel />
      {/* toast 宿主放最后：它是全局层，得盖在所有面板和对话框之上。 */}
      <Toaster />
    </div>
  );
}
