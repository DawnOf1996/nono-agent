import { FolderOpen, Settings2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { FieldLabel, Input } from "@/components/ui/input";
import { ModelSelect } from "@/features/chat/ModelSelect";
import { WorkspacePicker } from "@/features/session/WorkspacePicker";
import { useChatStore } from "@/store/chat";

export function NewTaskDialog() {
  const newTaskOpen = useChatStore((state) => state.newTaskOpen);
  const setNewTaskOpen = useChatStore((state) => state.setNewTaskOpen);
  const workspace = useChatStore((state) => state.workspace);
  const setWorkspace = useChatStore((state) => state.setWorkspace);
  const model = useChatStore((state) => state.model);
  const newSession = useChatStore((state) => state.newSession);
  const setSettingsOpen = useChatStore((state) => state.setSettingsOpen);
  const [pickerOpen, setPickerOpen] = useState(false);

  useEffect(() => {
    if (!newTaskOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (pickerOpen) return;
      event.preventDefault();
      setNewTaskOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newTaskOpen, pickerOpen, setNewTaskOpen]);

  if (!newTaskOpen) return null;

  function openModelSettings() {
    setNewTaskOpen(false);
    setSettingsOpen(true, "models");
  }

  return (
    <>
      <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-6">
        <div className="w-full max-w-lg rounded-2xl border border-line bg-bg p-5 shadow-xl">
        <h2 className="text-[15px] font-medium">新会话</h2>
        <p className="mt-1 text-[13px] leading-5 text-muted">
          文件读写会限制在所选目录。
        </p>

        <div className="mt-5">
          <FieldLabel htmlFor="workspace">工作空间</FieldLabel>
          <div className="flex items-center gap-2">
            <Input
              id="workspace"
              className="w-auto min-w-0 flex-1"
              value={workspace}
              placeholder="D:\WorkSpace\my-project"
              onChange={(event) => setWorkspace(event.target.value)}
            />
            <Button
              variant="outline"
              className="shrink-0 whitespace-nowrap"
              onClick={() => setPickerOpen(true)}
            >
              <FolderOpen className="size-4" />
              浏览
            </Button>
          </div>
        </div>

        <div className="mt-4">
          <div className="mb-1 flex items-center justify-between gap-3">
            <FieldLabel htmlFor="model" className="mb-0">
              模型
            </FieldLabel>
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-ink underline decoration-line-strong underline-offset-4 hover:bg-hover hover:decoration-ink"
              onClick={openModelSettings}
            >
              <Settings2 className="size-3.5" />
              配置模型
            </button>
          </div>
          <ModelSelect variant="field" id="model" />
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setNewTaskOpen(false)}>
            取消
          </Button>
          <Button
            onClick={() => void newSession({ cwd: workspace, model })}
            disabled={!workspace.trim() || !model.trim()}
          >
            开始
          </Button>
        </div>
        </div>
      </div>
      <WorkspacePicker open={pickerOpen} onClose={() => setPickerOpen(false)} />
    </>
  );
}
