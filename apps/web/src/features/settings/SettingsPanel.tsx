import { Bot, Boxes, Cable, Monitor, Moon, Puzzle, Settings2, SlidersHorizontal, Sun, X } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import type { AppInfo, SettingsTab } from "@nono/protocol";

import { openCatalogConfig } from "@/api/client";
import { Button } from "@/components/ui/button";
import { ConnectorSettings } from "@/features/settings/ConnectorSettings";
import { DataDirDialog } from "@/features/settings/DataDirDialog";
import { PresetPicker, ProviderList } from "@/features/settings/ModelSettings";
import { titleFromId, type ProviderPreset } from "@/features/settings/presets";
import {
  draftFromProvider,
  emptyDraft,
  ProviderEditor,
  type ProviderDraft,
} from "@/features/settings/ProviderEditor";
import { QuoteLimitField } from "@/features/settings/QuoteLimitSettings";
import { SidebarCards } from "@/features/settings/SidebarCards";
import { ToolPluginSettings } from "@/features/settings/ToolPluginSettings";
import { WorkspacePicker } from "@/features/session/WorkspacePicker";
import {
  CONTENT_FONT_SIZE_DEFAULT,
  CONTENT_FONT_SIZE_MAX,
  CONTENT_FONT_SIZE_MIN,
  getContentFontSize,
  setContentFontSize,
  subscribeContentFontSize,
} from "@/lib/content-font-size";
import {
  getThemeMode,
  setThemeMode,
  subscribeTheme,
  THEME_MODES,
  type ThemeMode,
} from "@/lib/theme";
import { registerToastAnchor, toast } from "@/lib/toast";
import {
  applyCheckResult,
  canCheckForUpdates,
  checkForUpdate,
  downloadUpdate,
  INITIAL_UPDATE_STATE,
  installUpdate,
  reduceUpdateEvent,
  subscribeUpdateEvents,
  type UpdateState,
} from "@/lib/updater";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

type ModelView = "list" | "presets" | "editor";

/** 字号滑块上的刻度数 = 可选的字号个数。 */
const FONT_SIZE_STEPS = CONTENT_FONT_SIZE_MAX - CONTENT_FONT_SIZE_MIN + 1;
/** 默认字号落在滑块的哪个位置，"默认"两个字要按这个摆。 */
const DEFAULT_PCT =
  ((CONTENT_FONT_SIZE_DEFAULT - CONTENT_FONT_SIZE_MIN) / (CONTENT_FONT_SIZE_MAX - CONTENT_FONT_SIZE_MIN)) * 100;

const TABS: Array<{ id: SettingsTab; label: string; icon: ReactNode; desc: string }> = [
  { id: "general", label: "通用设置", icon: <SlidersHorizontal className="size-4" />, desc: "外观、文件位置与侧边栏" },
  { id: "models", label: "模型", icon: <Boxes className="size-4" />, desc: "为各提供方配置 API 密钥与默认模型" },
  { id: "connectors", label: "连接器", icon: <Cable className="size-4" />, desc: "连接本地 MCP 服务与外部工具" },
  { id: "plugins", label: "插件", icon: <Puzzle className="size-4" />, desc: "开关与配置内置工具插件" },
  { id: "agents", label: "Agent 预设", icon: <Bot className="size-4" />, desc: "保存常用的系统提示词与工具组合" },
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
  // 连接器面板自己管着"列表 / 编辑某个"的状态，用回调把子页面的名字报上来给顶栏用。
  const [connectorTitle, setConnectorTitle] = useState<string | null>(null);
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

  // 把自己登记成 toast 的落点，提示就贴在这个面板底部而不是整个窗口底部。
  //
  // 这个 effect 必须写在下面那行 if (!settingsOpen) return null **之前** ——
  // hooks 不能条件调用，写在早退后面会直接违反 Hooks 规则。依赖只挂 settingsOpen：
  // 面板是整个挂载/卸载的，开着的时候节点不会换，没必要每次都重新登记。
  const panelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    return registerToastAnchor(panel);
  }, [settingsOpen]);

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

  // 顶栏是这一页唯一的标题。进了子页面（选预设 / 编辑提供方 / 编辑连接器）就用子页面的名字，
  // 否则会出现"模型"底下又顶着一个"模型"这种重复。
  const activeTab = TABS.find((item) => item.id === tab) ?? TABS[0];
  const subTitle =
    tab === "models"
      ? modelView === "presets"
        ? "添加提供方"
        : modelView === "editor"
          ? draft?.mode === "create"
            ? "自定义提供方"
            : "编辑提供方"
          : null
      : tab === "connectors" || tab === "plugins"
        ? connectorTitle
        : null;
  const headerTitle = subTitle ?? activeTab.label;
  const headerDesc = subTitle ? "" : activeTab.desc;

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-6"
      onClick={() => setSettingsOpen(false)}
    >
      <div
        ref={panelRef}
        // relative 是 toast 的 absolute 定位基准。少了它 toast 会去找更外层的
        // 定位祖先，最后落到整窗底部去 —— 正好是这个效果要避免的。
        className="relative flex h-[min(760px,90vh)] w-full max-w-[920px] overflow-hidden rounded-[28px] border border-line bg-bg shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        {/* 底色跟右侧面板一致（bg-bg 由外层面板给，这里不再叠一层 bg-sidebar），
            三条边线全部去掉。侧栏和内容区靠内容本身就能分开，再压一层线只会把
            面板切碎。选中项仍有 bg-active 加左侧竖条，不会因为去线而失去位置感。 */}
          <nav className="flex w-[212px] shrink-0 flex-col">
            <div className="flex h-14 shrink-0 items-center gap-2 px-4 text-sm font-medium">
              <Settings2 className="size-4 text-muted" />
              设置
            </div>
          <ul className="flex-1 space-y-0.5 overflow-auto p-2">
            {TABS.map((item) => {
              const active = tab === item.id;
              return (
                <li key={item.id} className="relative">
                  <button
                    type="button"
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex h-9 w-full items-center gap-2.5 rounded-lg px-3 text-sm transition-colors",
                      active ? "bg-active font-medium text-ink" : "text-muted hover:bg-hover hover:text-ink",
                    )}
                    onClick={() => {
                      setSettingsOpen(true, item.id);
                      if (item.id === "models") {
                        setModelView("list");
                        setDraft(null);
                      }
                    }}
                  >
                    <span className={active ? "text-ink" : "text-faint"}>{item.icon}</span>
                    {item.label}
                  </button>
                  {/* 选中项左侧的一小段竖条：底色之外再多一个方位线索，扫一眼就知道自己在哪一栏。 */}
                  {active ? <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-accent" /> : null}
                </li>
              );
            })}
          </ul>
          <div className="shrink-0 px-4 py-3 text-[11px] text-faint">
            {appVersionLabel()}
          </div>
        </nav>

        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line px-6">
            <div className="min-w-0">
              <h2 className="truncate text-[15px] font-medium text-ink">{headerTitle}</h2>
              {headerDesc ? <p className="truncate text-xs text-muted">{headerDesc}</p> : null}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button variant="outline" size="sm" className="rounded-full" onClick={() => void openConfig()}>
                打开配置文件
              </Button>
              <Button variant="ghost" size="icon" aria-label="关闭设置" onClick={() => setSettingsOpen(false)}>
                <X className="size-4" />
              </Button>
            </div>
          </header>
          {configError ? (
            <p className="shrink-0 border-b border-line px-6 py-2 text-xs text-danger">{configError}</p>
          ) : null}

          <div className="min-h-0 flex-1 overflow-auto px-6 py-5">
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

          {tab === "general" ? <GeneralPane info={info} /> : null}
          {tab === "connectors" ? (
            <ConnectorSettings onViewChange={setConnectorTitle} />
          ) : null}
          {tab === "plugins" ? (
            <ToolPluginSettings onViewChange={setConnectorTitle} />
          ) : null}
          {tab === "agents" ? (
            <ComingSoon detail="后续会在这里保存常用的系统提示词与工具组合。" />
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

/** 一组设置项。行与行之间靠一条细线分开，不再一张张画卡片 —— 线比框安静，也不会显得碎。 */
function SettingGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="text-[13px] font-medium text-muted">{label}</h3>
      <div className="mt-3 divide-y divide-line overflow-hidden rounded-2xl border border-line">{children}</div>
    </div>
  );
}

/** 一行设置：左边是名称和说明，右边是控件。 */
function SettingRow({
  label,
  hint,
  align = "center",
  children,
}: {
  label: string;
  hint?: string;
  /**
   * 右侧控件占多行时用 "start"：让标签跟右侧**第一行**对齐，而不是跟整块居中。
   *
   * 默认的 "center" 是给单行控件准备的 —— 两列一样高时居中才好看。一旦右边长出
   * 第二行（比如更新状态说明），居中就会把标签夹在两行中间，看上去像是飘了起来。
   */
  align?: "center" | "start";
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex justify-between gap-6 px-4 py-3",
        align === "center" ? "items-center" : "items-start",
      )}
    >
      {/* start 模式下把标签这块撑到至少和 sm 按钮一样高（h-8），文字在里面居中。
          这样对齐不依赖文字本身的行高，换字号或改字体也不会再错位。
          min-h-8 而不是 h-8：带 hint 的行还能往下长。 */}
      <div className={cn("shrink-0", align === "start" && "flex min-h-8 flex-col justify-center")}>
        <div className="text-[13px] text-ink">{label}</div>
        {hint ? <div className="mt-0.5 text-[11px] text-muted">{hint}</div> : null}
      </div>
      <div className="min-w-0 text-right">{children}</div>
    </div>
  );
}

/** 分区之间的那条线。给整块内容一个统一的呼吸节奏。 */
function PaneSection({ children }: { children: ReactNode }) {
  return <section className="mt-7 border-t border-line pt-6">{children}</section>;
}

/**
 * 更新状态机。事件全部由主进程推过来（见 desktop/src/main.js 的 sendUpdateEvent），
 * 渲染进程自己不碰网络。能不能更新取决于两件事：preload 有没有暴露桥接
 * （纯 web 打开没有），以及主进程是不是打包态（Electron 开发态没有
 * app-update.yml，主进程会回 unsupported）。
 */
function useUpdater() {
  const [state, setState] = useState<UpdateState>(INITIAL_UPDATE_STATE);
  // 这次检查是不是用户自己点的。只有这种才值得弹 toast：启动时主进程也会静默查
  // 一次并同样走到 latest，那会儿没人等着听结果，开局就飘一句「已是最新」纯属噪音。
  const userChecked = useRef(false);

  useEffect(
    () => subscribeUpdateEvents((event) => setState((prev) => reduceUpdateEvent(prev, event))),
    [],
  );

  const supported = canCheckForUpdates();

  // 「已是最新」在那一行里只表现为按钮从「检查更新」变成「重新检查」，太轻了，
  // 用户很容易以为没点上。所以用户主动查的时候补一个 toast。
  //
  // 放 useEffect 而不是塞进上面的 setState 里：state updater 属于纯计算，React 在
  // StrictMode 下会调两次，在里面弹 toast 会弹两次。
  useEffect(() => {
    if (!userChecked.current) return;
    // 还在查就别急，也别把标记清了 —— 结果还没到。
    if (state.kind === "checking" || state.kind === "idle") return;
    // 无论落到哪个终态都消费掉这次标记，否则下次不相干的 latest 会误报。
    userChecked.current = false;
    if (state.kind === "latest") {
      toast(state.version ? `已是最新版本 ${state.version}` : "已是最新版本");
    }
  }, [state]);

  async function check() {
    // 先乐观切到「检查中」，不等 IPC 往返 —— 点了就得立刻有反应。
    setState({ kind: "checking" });
    userChecked.current = true;
    const result = await checkForUpdate();
    // 回执不能无条件落地：事件一定比它先到，落地就会把「有新版本」盖回「检查中」，
    // 而检查已经结束不会再有事件纠正。细节见 applyCheckResult 的注释。
    setState((prev) => applyCheckResult(prev, result));
  }

  async function download() {
    // 立刻把按钮切到「下载中」，不等主进程第一个 progress 事件 —— 那可能有一两秒。
    setState({ kind: "downloading", percent: 0 });
    if (!(await downloadUpdate())) {
      setState({ kind: "error", message: "下载没能开始，请重试" });
    }
  }

  async function install() {
    if (!(await installUpdate())) {
      setState({ kind: "error", message: "安装没能启动，请手动重启应用" });
    }
  }

  return { state, supported, check, download, install };
}

function GeneralPane({
  info,
}: {
  info: AppInfo | null;
}) {
  const dataDir = info?.data_dir ?? "";
  const [picking, setPicking] = useState(false);
  // 选完目录不直接搬：先过 DataDirDialog 确认，避免「点一下就不可逆地搬走几百 MB」。
  const [pendingTarget, setPendingTarget] = useState<string | null>(null);
  const updater = useUpdater();

  // 一个按钮扛完 查 → 报结果 → 下载 → 装 整条流程。文案跟着状态走，
  // 用户点之前就知道会发生什么。
  const updateButton = (() => {
    switch (updater.state.kind) {
      case "checking":
        return { label: "检查中…", disabled: true, action: updater.check };
      case "latest":
        return { label: "重新检查", disabled: false, action: updater.check };
      case "available":
        return { label: "下载更新", disabled: false, action: updater.download };
      case "downloading":
        return { label: `下载中 ${updater.state.percent}%`, disabled: true, action: updater.download };
      case "downloaded":
        return { label: "重启并安装", disabled: false, action: updater.install };
      case "error":
        return { label: "重试", disabled: false, action: updater.check };
      default:
        // idle 和 unsupported 都落这儿，按钮必须一直在。手动触发是这个功能的
        // 主入口 —— 启动时那次静默检查只是锦上添花，没跑或者失败了，用户还得
        // 能自己点一下。曾经这里 return null，把「还没查」和「开发态」都变成
        // 了整行只剩一个版本号，等于把按钮删了。
        return { label: "检查更新", disabled: false, action: updater.check };
    }
  })();

  const updateNote = (() => {
    switch (updater.state.kind) {
      case "available":
        return `发现新版本 ${updater.state.version}`;
      case "downloading":
        return "正在下载安装包";
      case "downloaded":
        return `新版本 ${updater.state.version} 已下载，重启后生效`;
      case "error":
        return `没查成：${updater.state.message}`;
      case "unsupported":
        // 两种情况都会落这儿：浏览器里打开（没有 preload 桥），以及 Electron
        // 开发态（桥在，但主进程没有 app-update.yml，不会真去查）。
        return "浏览器打开或开发模式下不支持更新检查";
      default:
        return null;
    }
  })();

  return (
    <div className="max-w-[660px]">
      <AppearanceSettings />

      <PaneSection>
        <SettingGroup label="存储">
          <SettingRow
            label="数据目录"
            hint="模型、密钥、会话、记忆和插件都在这里。更换会把全部内容搬过去"
          >
            <div className="flex items-center gap-3">
              {/* 路径换行会让这一行在 660px 里能塞下更多字符，但纯路径折成两三行
                  时「哪一坨是目录名」很难读。这里单行截断 + title 挂全串。 */}
              <span
                className="truncate font-mono text-[12px] text-muted"
                title={dataDir || "~/.nono-agent"}
              >
                {dataDir || "~/.nono-agent"}
              </span>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0 rounded-full"
                onClick={() => setPicking(true)}
              >
                更改
              </Button>
            </div>
          </SettingRow>
        </SettingGroup>
      </PaneSection>

      <PaneSection>
        <SettingGroup label="对话">
          <SettingRow
            label="引用条数上限"
            hint="在回复或文件预览里选中文字后，一次最多能挂多少条引用和评论"
          >
            <QuoteLimitField />
          </SettingRow>
        </SettingGroup>
      </PaneSection>

      <PaneSection>
        <SidebarCards />
      </PaneSection>

      <PaneSection>
        <SettingGroup label="关于">
          {/* align="start"：状态说明冒出来时右边会变成两行，标签得跟「版本号 +
              按钮」那一行对齐，跟整块居中会看着像飘上去了。 */}
          <SettingRow label="当前版本" align="start">
            <div className="flex flex-col items-end gap-1">
              <div className="flex items-center justify-end gap-3">
                <span className="text-[13px] text-muted">{appVersionLabel()}</span>
                <Button
                  variant="outline"
                  size="sm"
                  className="rounded-full"
                  disabled={updateButton.disabled}
                  onClick={updateButton.action}
                >
                  {updateButton.label}
                </Button>
              </div>
              {/* 状态说明挂在按钮下面，而不是塞进 SettingRow 的 hint：hint 说的是
                  这一行固定是什么，「有新版」是此刻的临时状态，不该长期占着 hint。 */}
              {updateNote ? <div className="text-[11px] text-muted">{updateNote}</div> : null}
            </div>
          </SettingRow>
        </SettingGroup>
      </PaneSection>

      <WorkspacePicker
        open={picking}
        onClose={() => setPicking(false)}
        title="选择数据目录"
        confirmLabel="迁移到这里"
        // 浏览起点必须是「已存在」的目录：initialPath 的语义是打开时先进入这里。
        // 不能拿推荐目标当起点 —— 那是个还不存在的路径，列表接口会 404，用户每次
        // 打开都先撞一句「打不开这个目录」。想搬到哪，让用户自己在树里挑或新建。
        initialPath={dataDir || undefined}
        onConfirm={(next) => {
          setPicking(false);
          setPendingTarget(next);
        }}
      />
      {pendingTarget ? (
        <DataDirDialog
          target={pendingTarget}
          current={dataDir || "~/.nono-agent"}
          onClose={() => setPendingTarget(null)}
          onMigrated={(next) => {
            // 顶部立刻显示新路径，但真正生效要等重启 —— 弹窗里的提示一直在，
            // 不会被这里覆盖掉。
            const current = useChatStore.getState().info;
            if (current) {
              useChatStore.setState({
                info: { ...current, data_dir: next, data_dir_is_default: false },
              });
            }
          }}
        />
      ) : null}
    </div>
  );
}

/** 外观三选一。选中项用 active 底色 + line-strong 描边，跟设置面板里其他"选中"样式一致。 */
function ThemeIcon({ id, className }: { id: ThemeMode; className?: string }) {
  if (id === "light") return <Sun className={className} />;
  if (id === "dark") return <Moon className={className} />;
  return <Monitor className={className} />;
}

/**
 * 外观 + 会话正文字号。
 * 两项都是"改完立刻生效"的：主题写 <html data-theme>，字号写 :root 上的 --content-font-size，
 * 所以改完不用关面板，也不用刷新。
 */
function AppearanceSettings() {
  const mode = useSyncExternalStore(subscribeTheme, getThemeMode, getThemeMode);
  const size = useSyncExternalStore(subscribeContentFontSize, getContentFontSize, getContentFontSize);
  // 滑块没有"已选段上色"的接口，得把当前值换算成百分比交给 CSS。
  const pct = ((size - CONTENT_FONT_SIZE_MIN) / (CONTENT_FONT_SIZE_MAX - CONTENT_FONT_SIZE_MIN)) * 100;
  return (
    <div>
      <h3 className="text-[13px] font-medium text-muted">外观</h3>
      <div className="mt-3 grid grid-cols-3 gap-3">
        {THEME_MODES.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-pressed={mode === item.id}
            className={cn(
              "flex flex-col items-center gap-2 rounded-lg border px-2 py-4 text-[12px] transition-colors",
              mode === item.id
                ? "border-line-strong bg-active text-ink"
                : "border-line bg-bg text-muted hover:bg-hover hover:text-ink",
            )}
            onClick={() => setThemeMode(item.id)}
          >
            <ThemeIcon id={item.id} className="size-4" />
            {item.label}
          </button>
        ))}
      </div>

      {/* 主题卡片和字号之间也走一条线：同一分组里换了一个维度，得让人一眼看出来。 */}
      <div className="mt-5 flex items-center justify-between gap-6 border-t border-line pt-5">
        <div className="shrink-0">
          <div className="text-[13px] text-ink">字号大小</div>
          <div className="mt-0.5 text-[11px] text-muted">仅影响会话内容的字号</div>
        </div>
        <div className="w-[300px] shrink-0">
          <div className="relative">
            {/* 刻度：每个可选字号一根短线。两端各让开半个拇指宽，免得压在圆点上。 */}
            <div
              className="pointer-events-none absolute inset-x-[7px] top-1/2 flex -translate-y-1/2 justify-between"
              aria-hidden="true"
            >
              {Array.from({ length: FONT_SIZE_STEPS }, (_, index) => (
                <span key={index} className="h-2.5 w-px bg-line-strong" />
              ))}
            </div>
            <input
              type="range"
              min={CONTENT_FONT_SIZE_MIN}
              max={CONTENT_FONT_SIZE_MAX}
              step={1}
              value={size}
              aria-label="字号大小"
              className="font-size-slider"
              style={{ "--pct": `${pct}%` } as CSSProperties}
              onChange={(event) => setContentFontSize(Number(event.target.value))}
            />
          </div>
          <div className="relative mt-1.5 h-4 text-[11px] text-faint">
            <span className="absolute left-0">小</span>
            {/* "默认"落在 14px 真正对应的位置上，点一下就回到默认。 */}
            <button
              type="button"
              className="absolute -translate-x-1/2 transition-colors hover:text-ink"
              style={{ left: `${DEFAULT_PCT}%` }}
              onClick={() => setContentFontSize(CONTENT_FONT_SIZE_DEFAULT)}
            >
              默认
            </button>
            <span className="absolute right-0">大</span>
          </div>
        </div>
      </div>
      <p className="mt-4 text-ink" style={{ fontSize: size }}>
        预览：这段对话应该跟正文一样大。The quick brown fox jumps.
      </p>
    </div>
  );
}

function ComingSoon({ detail }: { detail: string }) {
  return (
    <p className="rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">
      {detail}
    </p>
  );
}
