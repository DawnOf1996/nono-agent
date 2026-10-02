import { useEffect } from "react";

import { PermissionDialog } from "@/features/permissions/PermissionDialog";
import { NewTaskDialog } from "@/features/session/NewTaskDialog";
import { SettingsPanel } from "@/features/settings/SettingsPanel";
import { Workbench } from "@/features/shell/Workbench";
import { useChatStore } from "@/store/chat";

export function App() {
  const refresh = useChatStore((state) => state.refresh);
  const watchControl = useChatStore((state) => state.watchControl);
  const unwatchControl = useChatStore((state) => state.unwatchControl);
  const watchSessionStats = useChatStore((state) => state.watchSessionStats);
  const unwatchSessionStats = useChatStore((state) => state.unwatchSessionStats);
  const sessionId = useChatStore((state) => state.sessionId);

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

  return (
    <div className="flex h-full min-h-0 bg-bg">
      <Workbench />
      <PermissionDialog />
      <NewTaskDialog />
      <SettingsPanel />
    </div>
  );
}
