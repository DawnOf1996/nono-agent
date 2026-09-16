import { FolderOpen } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FieldLabel, Input, Select } from "@/components/ui/input";
import { pickWorkspace } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

export function NewTaskDialog() {
  const newTaskOpen = useChatStore((state) => state.newTaskOpen);
  const setNewTaskOpen = useChatStore((state) => state.setNewTaskOpen);
  const workspace = useChatStore((state) => state.workspace);
  const setWorkspace = useChatStore((state) => state.setWorkspace);
  const model = useChatStore((state) => state.model);
  const setModel = useChatStore((state) => state.setModel);
  const catalog = useChatStore((state) => state.catalog);
  const newSession = useChatStore((state) => state.newSession);
  const setSettingsOpen = useChatStore((state) => state.setSettingsOpen);

  if (!newTaskOpen) return null;

  async function browse() {
    const path = await pickWorkspace(workspace);
    if (path) setWorkspace(path);
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-6">
      <div className="w-full max-w-lg rounded-2xl border border-line bg-bg p-5 shadow-xl">
        <div className="text-xs text-faint">新会话</div>
        <h2 className="mt-1 text-lg font-medium">选一个工作空间，再选模型</h2>
        <p className="mt-1 text-sm leading-6 text-muted">
          工具读写都落在这个目录里。桌面端可浏览文件夹；浏览器里请粘贴绝对路径。
        </p>

        <div className="mt-4">
          <FieldLabel htmlFor="workspace">工作空间</FieldLabel>
          <div className="flex gap-2">
            <Input
              id="workspace"
              value={workspace}
              placeholder="D:\WorkSpace\my-project"
              onChange={(event) => setWorkspace(event.target.value)}
            />
            <Button variant="outline" onClick={() => void browse()}>
              <FolderOpen className="size-4" />
              浏览
            </Button>
          </div>
        </div>

        <div className="mt-4">
          <FieldLabel htmlFor="model">模型</FieldLabel>
          <Select id="model" value={model} onChange={(event) => void setModel(event.target.value)}>
            {!model ? <option value="">选择模型</option> : null}
            {(catalog?.providers ?? []).map((provider) => (
              <optgroup key={provider.id} label={provider.display_name}>
                {provider.models.map((item) => (
                  <option key={`${provider.id}:${item.id}`} value={item.id}>
                    {item.name || item.id}
                    {provider.has_key ? "" : "（缺密钥）"}
                  </option>
                ))}
              </optgroup>
            ))}
          </Select>
          <button
            type="button"
            className="mt-2 text-xs text-muted hover:text-ink"
            onClick={() => {
              setNewTaskOpen(false);
              setSettingsOpen(true);
            }}
          >
            配置模型…
          </button>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setNewTaskOpen(false)}>
            取消
          </Button>
          <Button onClick={() => void newSession({ cwd: workspace, model })} disabled={!workspace.trim()}>
            开始
          </Button>
        </div>
      </div>
    </div>
  );
}
