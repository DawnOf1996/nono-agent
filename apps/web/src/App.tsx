import { useEffect } from "react";

import { ChatPane } from "@/features/chat/ChatPane";
import { PermissionDialog } from "@/features/permissions/PermissionDialog";
import { NewTaskDialog } from "@/features/session/NewTaskDialog";
import { SessionRail } from "@/features/session/SessionRail";
import { SettingsPanel } from "@/features/settings/SettingsPanel";
import { useChatStore } from "@/store/chat";

export function App() {
  const refresh = useChatStore((state) => state.refresh);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return (
    <div className="flex h-full min-h-0">
      <SessionRail />
      <ChatPane />
      <PermissionDialog />
      <NewTaskDialog />
      <SettingsPanel />
    </div>
  );
}
