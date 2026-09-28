import { Bot, Boxes, Cable, Puzzle, Settings2, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { SettingsTab } from "@nono/protocol";

import { openCatalogConfig, updateSessionDir } from "@/api/client";
import { Button } from "@/components/ui/button";
import { ConnectorSettings } from "@/features/settings/ConnectorSettings";
import { PresetPicker, ProviderList } from "@/features/settings/ModelSettings";
import { titleFromId, type ProviderPreset } from "@/features/settings/presets";
import {
  draftFromProvider,
  emptyDraft,
  ProviderEditor,
  type ProviderDraft,
} from "@/features/settings/ProviderEditor";
import { SidebarCards } from "@/features/settings/SidebarCards";
import { WorkspacePicker } from "@/features/session/WorkspacePicker";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

type ModelView = "list" | "presets" | "editor";

const TABS: Array<{ id: SettingsTab; label: string; icon: ReactNode }> = [
  { id: "general", label: "通用设置", icon: <SlidersHorizontal className="size-4" /> },
  { id: "models", label: "模型", icon: <Boxes className="size-4" /> },
  { id: "connectors", label: "连接器", icon: <Cable className="size-4" /> },
  { id: "plugins", label: "插件", icon: <Puzzle className="size-4" /> },
  { id: "agents", label: "Agent 预设", icon: <Bot className="size-4" /> },
];

export function SettingsPanel() {
  const settingsOpen = useChatStore((state) => state.settingsOpen);
  const settingsTab = useChatStore((state) => state.settingsTab);
  const setSettingsOpen = useChatStore((state) => state.setSettingsOpen);
  const catalog = useChatStore((state) => state.catalog);
  const info = useChatStore((state) => state.info);
  const upsertProvider = useChatStore((state) => state.upsertProvider);
  const removeProvider = useChatStore((state) => state.removeProvider);
  const chooseDefault = useChatStore((state) => state.chooseDefault);
  const setModel = useChatStore((state) => state.setModel);
  const [modelView, setModelView] = useState<ModelView>("list");
  const [draft, setDraft] = useState<ProviderDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);
  const tab = settingsTab;

  useEffect(() => {
    if (!settingsOpen) return;
    setModelView("list");
    setDraft(null);
    setConfigError(null);
  }, [settingsOpen, settingsTab]);

  useEffect(() => {
    if (!settingsOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setSettingsOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [settingsOpen, setSettingsOpen]);

  if (!settingsOpen) return null;

  const providers = catalog?.providers ?? [];
  const adapters = catalog?.adapters ?? [];

  function openCustom(next?: Partial<ProviderDraft>) {
    setDraft(
      emptyDraft(adapters[0] ?? "openai_chat_completions", {
        ...next,
        display_name: next?.display_name || (next?.id ? titleFromId(next.id) : ""),
      }),
    );
    setModelView("editor");
  }

  function openPreset(preset: ProviderPreset) {
    openCustom({
      id: preset.id,
      display_name: preset.display_name,
      api: preset.api,
      base_url: preset.base_url,
    });
  }

  async function saveDraft(body: Parameters<typeof upsertProvider>[0]) {
    setSaving(true);
    try {
      await upsertProvider(body);
      setModelView("list");
      setDraft(null);
    } finally {
      setSaving(false);
    }
  }

  async function deleteCurrent() {
    if (!draft || draft.mode !== "edit") return;
    await removeProvider(draft.id);
    setModelView("list");
    setDraft(null);
  }

  async function openConfig() {
    setConfigError(null);
    try {
      await openCatalogConfig();
    } catch (error) {
      setConfigError(error instanceof Error ? error.message : "无法打开配置文件");
    }
  }

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-6"
      onClick={() => setSettingsOpen(false)}
    >
      <div
        className="flex h-[min(760px,90vh)] w-full max-w-[920px] overflow-hidden rounded-[28px] border border-line bg-bg shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <nav className="flex w-[200px] shrink-0 flex-col border-r border-line bg-sidebar px-3 py-4">
          <div className="flex items-center gap-2 px-2 pb-4 text-sm font-medium">
            <Settings2 className="size-4 text-muted" />
            设置
          </div>
          <ul className="space-y-0.5">
            {TABS.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={cn(
                    "flex h-9 w-full items-center gap-2 rounded-lg px-2.5 text-sm text-muted transition-colors hover:bg-hover hover:text-ink",
                    tab === item.id && "bg-white text-ink shadow-sm",
                  )}
                  onClick={() => {
                    setSettingsOpen(true, item.id);
                    if (item.id === "models") {
                      setModelView("list");
                      setDraft(null);
                    }
                  }}
                >
                  {item.icon}
                  {item.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <section className="flex min-h-0 min-w-0 flex-1 flex-col px-6 py-4">
          <div className="mb-4 flex shrink-0 items-center justify-end gap-2">
            <Button variant="outline" size="sm" className="rounded-full" onClick={() => void openConfig()}>
              打开配置文件
            </Button>
            <Button variant="ghost" size="icon" onClick={() => setSettingsOpen(false)}>
              <X className="size-4" />
            </Button>
          </div>
          {configError ? <p className="mb-3 shrink-0 text-xs text-danger">{configError}</p> : null}

          <div className="min-h-0 flex-1 overflow-auto">
          {tab === "models" && modelView === "list" ? (
            <ProviderList
              providers={providers}
              defaultProvider={catalog?.default?.provider}
              defaultModel={catalog?.default?.model}
              onEdit={(provider) => {
                setDraft(draftFromProvider(provider));
                setModelView("editor");
              }}
              onChooseDefault={(providerId, modelId) => {
                void setModel(modelId);
                void chooseDefault(providerId, modelId);
              }}
              onAddPreset={() => setModelView("presets")}
              onAddCustom={() => openCustom()}
            />
          ) : null}

          {tab === "models" && modelView === "presets" ? (
            <PresetPicker
              existingIds={providers.map((item) => item.id)}
              onPick={openPreset}
              onCancel={() => setModelView("list")}
            />
          ) : null}

          {tab === "models" && modelView === "editor" && draft ? (
            <ProviderEditor
              key={`${draft.mode}:${draft.id}:${draft.api}:${draft.base_url}`}
              draft={draft}
              adapters={adapters}
              existingIds={providers.map((item) => item.id)}
              saving={saving}
              onCancel={() => {
                setDraft(null);
                setModelView("list");
              }}
              onSave={saveDraft}
              onDelete={draft.mode === "edit" ? deleteCurrent : undefined}
            />
          ) : null}

          {tab === "general" ? (
            <GeneralPane configPath={catalog?.config_path} info={info} />
          ) : null}
          {tab === "connectors" ? <ConnectorSettings /> : null}
          {tab === "plugins" ? (
            <ComingSoon title="插件" detail="后续会在这里管理本地插件。" />
          ) : null}
          {tab === "agents" ? (
            <ComingSoon title="Agent 预设" detail="后续会在这里保存常用的系统提示词与工具组合。" />
          ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function appVersionLabel(): string {
  const version = window.nonoDesktop?.version || __NONO_APP_VERSION__;
  return version ? `NoNo ${version}` : "NoNo";
}

function GeneralPane({
  configPath,
  info,
}: {
  configPath?: string | null;
  info: { session_dir?: string } | null;
}) {
  const [path, setPath] = useState(info?.session_dir ?? "");
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    if (info?.session_dir) setPath(info.session_dir);
  }, [info?.session_dir]);

  async function chooseDirectory(next: string) {
    const result = await updateSessionDir(next);
    setPath(result.session_dir);
    const current = useChatStore.getState().info;
    if (current) useChatStore.setState({ info: { ...current, session_dir: result.session_dir } });
  }

  return (
    <div>
      <h3 className="text-[15px] font-medium">通用设置</h3>
      <p className="mt-1 text-[13px] text-muted">模型和密钥保存在本机用户目录，不会写入对话记录。</p>
      <dl className="mt-5 space-y-3 text-sm">
        <div className="rounded-2xl border border-line px-4 py-3">
          <dt className="text-xs text-muted">配置文件</dt>
          <dd className="mt-1 break-all font-mono text-[12px] text-ink">
            {configPath || "~/.nono-agent/config.yaml"}
          </dd>
        </div>
        <div className="rounded-2xl border border-line px-4 py-3">
          <dt className="flex items-center justify-between gap-2 text-xs text-muted">
            对话文件
            <button
              type="button"
              className="text-[12px] text-ink hover:underline"
              onClick={() => setPicking(true)}
            >
              更改
            </button>
          </dt>
          <dd className="mt-1 break-all font-mono text-[12px] text-ink">
            {path || "~/.nono-agent/sessions"}
          </dd>
        </div>
        <div className="rounded-2xl border border-line px-4 py-3">
          <dt className="text-xs text-muted">版本</dt>
          <dd className="mt-1 text-[13px]">{appVersionLabel()}</dd>
        </div>
      </dl>
      <SidebarCards />
      <WorkspacePicker
        open={picking}
        onClose={() => setPicking(false)}
        title="选择对话文件目录"
        confirmLabel="迁移到这里"
        initialPath={path || "~/.nono-agent/sessions"}
        onConfirm={chooseDirectory}
      />
    </div>
  );
}

function ComingSoon({ title, detail }: { title: string; detail: string }) {
  return (
    <div>
      <h3 className="text-[15px] font-medium">{title}</h3>
      <p className="mt-3 rounded-2xl border border-dashed border-line px-4 py-8 text-sm text-muted">{detail}</p>
    </div>
  );
}
