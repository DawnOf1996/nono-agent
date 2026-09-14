import { FolderOpen } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FieldLabel, Input, Select } from "@/components/ui/input";
import { pickWorkspace } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

export function NewTaskDialog() {
  const {
    newTaskOpen,
    setNewTaskOpen,
    workspace,
    setWorkspace,
    model,
    setModel,
    catalog,
    newSession,
    setSettingsOpen,
  } = useChatStore();

  if (!newTaskOpen) return null;

  async function browse() {
    const path = await pickWorkspace(workspace);
    if (path) setWorkspace(path);
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/55 p-6">
      <div className="w-full max-w-lg border border-line bg-ink p-5">
        <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-copper">新任务</div>
        <h2 className="mt-2 text-lg font-medium">选一个工作空间，再选模型</h2>
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
          <Select
            id="model"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          >
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
            className="mt-2 text-xs text-steel hover:text-copper"
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
          <Button
            onClick={() => void newSession({ cwd: workspace, model })}
            disabled={!workspace.trim()}
          >
            开始
          </Button>
        </div>
      </div>
    </div>
  );
}
