import { useEffect } from "react";

import { ChatPane } from "@/features/chat/ChatPane";
import { PermissionDialog } from "@/features/permissions/PermissionDialog";
import { NewTaskDialog } from "@/features/session/NewTaskDialog";
import { SettingsPanel } from "@/features/settings/SettingsPanel";
import { Sidebar } from "@/features/shell/Sidebar";
import { useChatStore } from "@/store/chat";

export function App() {
  const refresh = useChatStore((state) => state.refresh);
  const refreshHealth = useChatStore((state) => state.refreshHealth);
  const refreshPermissions = useChatStore((state) => state.refreshPermissions);
  const streaming = useChatStore((state) => state.streaming);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshHealth();
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [refreshHealth]);

  useEffect(() => {
    if (!streaming) return;
    void refreshPermissions();
    const timer = window.setInterval(() => {
      void refreshPermissions();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [streaming, refreshPermissions]);

  return (
    <div className="flex h-full min-h-0 bg-bg">
      <Sidebar />
      <ChatPane />
      <PermissionDialog />
      <NewTaskDialog />
      <SettingsPanel />
    </div>
  );
}
