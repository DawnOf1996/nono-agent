import {
  AlertCircle,
  Check,
  Copy,
  KeyRound,
  Loader2,
  Pencil,
  Puzzle,
  Terminal,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { ToolPluginField, ToolPluginInfo } from "@nono/protocol";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dropdown, DropdownItem, FieldSelectButton } from "@/components/ui/dropdown";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { writeClipboardText } from "@/lib/clipboard";
import { toast } from "@/lib/toast";
import { PendingRestartDialog } from "@/features/settings/RestartDialog";
import { useChatStore } from "@/store/chat";

/**
 * 工具插件的配置面板。
 *
 * **这一层不认识任何具体插件**：字段、控件类型、默认值、示例句全部来自服务端按
 * 插件清单生成的 schema。要支持一个新插件，在它的 plugin.json 里加一段 `config`
 * 或 `examples` 就行，前端不用改 —— 否则每加一个厂商就要在这里加一段 if。
 */
export function ToolPluginSettings({ onViewChange }: { onViewChange?: (title: string | null) => void }) {
  const plugins = useChatStore((state) => state.toolPlugins);
  const credentialsPath = useChatStore((state) => state.toolPluginCredentialsPath);
  const settingsPath = useChatStore((state) => state.toolPluginSettingsPath);
  const refresh = useChatStore((state) => state.refreshToolPlugins);
  const save = useChatStore((state) => state.saveToolPlugin);
  const saveKey = useChatStore((state) => state.saveToolPluginApiKey);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 哪个插件正等着重启确认。存 id 而不是布尔量：插件列表可能同时有两个
  // needs_restart 的插件各自改了开关，弹一个会把另一个的上下文说错。
  const [restartPrompt, setRestartPrompt] = useState<string | null>(null);
  // 哪个插件的详情弹窗开着。跟 editing 分开两个状态：配置表单要占满整块面板，
  // 详情是一次性浮层，两者同时存在只会互相打架。
  const [detailId, setDetailId] = useState<string | null>(null);

  const current = plugins.find((item) => item.id === editing) ?? null;
  const restarting = plugins.find((item) => item.id === restartPrompt) ?? null;
  const detailed = plugins.find((item) => item.id === detailId) ?? null;

  useEffect(() => {
    onViewChange?.(current ? `配置 ${current.id}` : null);
    return () => onViewChange?.(null);
  }, [current, onViewChange]);

  // 面板打开时拉一次。开关会改运行时状态，服务端返回的就是生效后的状态。
  useEffect(() => {
    void refresh();
    // 只在挂载时拉一次；后续靠各操作返回的最新快照。
  }, [refresh]);

  async function toggle(plugin: ToolPluginInfo, enabled: boolean) {
    setBusy(plugin.id);
    setError(null);
    try {
      await save(plugin.id, { enabled });
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(null);
    }
  }

  if (current) {
    return (
      <PluginEditor
        plugin={current}
        busy={busy === current.id}
        error={error}
        onBack={() => {
          setEditing(null);
          setError(null);
        }}
        onSave={async (options) => {
          setBusy(current.id);
          setError(null);
          try {
            await save(current.id, { options });
            setEditing(null);
          } catch (err) {
            setError(err instanceof Error ? err.message : "保存失败");
          } finally {
            setBusy(null);
          }
        }}
        onSaveKey={async (apiKey) => {
          setBusy(current.id);
          setError(null);
          try {
            await saveKey(current.id, apiKey);
          } catch (err) {
            setError(err instanceof Error ? err.message : "保存密钥失败");
          } finally {
            setBusy(null);
          }
        }}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <p className="text-[13px] text-muted">
        打开开关并填好密钥后，插件提供的工具会直接出现在对话的工具列表里。每个插件能配什么，由它自己的清单决定。
      </p>
      <p className="mt-1 text-[11px] text-faint">
        开关和参数是<strong className="font-medium text-muted">全局</strong>的：在任意工作区改一次，所有工作区都跟着变。
      </p>

      <div className="mt-4 min-h-0 flex-1 space-y-2 overflow-auto pr-1">
        {plugins.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">
            没有发现内置插件。
          </div>
        ) : (
          plugins.map((plugin) => (
            <PluginCard
              key={plugin.id}
              plugin={plugin}
              busy={busy === plugin.id}
              onToggle={(next) => void toggle(plugin, next)}
              onConfigure={() => setEditing(plugin.id)}
              onRequestRestart={() => setRestartPrompt(plugin.id)}
              onShowDetail={() => setDetailId(plugin.id)}
            />
          ))
        )}
      </div>

      {error ? <p className="mt-3 text-xs text-danger">{error}</p> : null}

      <div className="mt-4 flex flex-col gap-1">
        <p className="truncate font-mono text-[11px] text-faint" title={settingsPath ?? undefined}>
          开关与参数 {settingsPath || "~/.nono-agent/settings.json"}
        </p>
        <p className="truncate font-mono text-[11px] text-faint" title={credentialsPath ?? undefined}>
          密钥 {credentialsPath || "~/.nono-agent/credentials"}
        </p>
      </div>

      {restarting ? (
        <PendingRestartDialog
          title={restarting.enabled ? `${restarting.id} 已开启` : `${restarting.id} 已关闭`}
          description={
            restarting.enabled
              ? "这个能力由应用在启动前挂上，要重启才会真的开始工作。现在就重启吗？"
              : "它会一直保持现状直到应用重启。重启之后这个插件将不再生效。"
          }
          onClose={() => setRestartPrompt(null)}
        />
      ) : null}

      {detailed ? (
        <PluginDetailDialog
          plugin={detailed}
          onClose={() => setDetailId(null)}
          onConfigure={() => {
            setDetailId(null);
            setEditing(detailed.id);
          }}
        />
      ) : null}
    </div>
  );
}

function PluginCard({
  plugin,
  busy,
  onToggle,
  onConfigure,
  onRequestRestart,
  onShowDetail,
}: {
  plugin: ToolPluginInfo;
  busy: boolean;
  onToggle: (next: boolean) => void;
  onConfigure: () => void;
  onRequestRestart: () => void;
  onShowDetail: () => void;
}) {
  // 有字段或要填密钥的插件才给"配置"入口；只有一个开关的插件直接就地开关。
  const configurable = plugin.fields.length > 0 || plugin.api_key.required;
  const needsKey = plugin.api_key.required && !plugin.api_key.configured;
  // 工作区里手写的配置会盖住全局开关。不说出来的话，用户会对着一个
  // 怎么按都没反应的开关反复点。
  const overridden = plugin.project_owned ?? [];
  // 用户改了但还没重启：本次运行的真实状态与开关不一致。
  const pendingRestart = plugin.needs_restart && !plugin.live;

  return (
    <div className="rounded-2xl border border-line bg-bg px-4 py-3">
      <div className="flex items-center gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-card text-muted">
          <Puzzle className="size-4" />
        </span>
        {/* 整块可点而不是只把 id 做成链接：命中区域大得多，用户不必瞄准那几个
            像素；同时保留可聚焦性，键盘能进得来。 */}
        <button
          type="button"
          onClick={onShowDetail}
          className="group min-w-0 flex-1 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/15"
        >
          <span className="flex items-center gap-2">
            <span className="truncate text-sm font-medium group-hover:underline">
              {plugin.id}
            </span>
            <SourceBadge source={plugin.source} />
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-faint">
            {plugin.tools.length > 0 ? plugin.tools.join(" · ") : plugin.description || "尚未提供工具"}
          </span>
        </button>
        {/*
          按钮位**始终**占位，没有可配置项的插件用 invisible 藏起来，而不是直接
          不渲染 —— 不渲染的话开关那一列会跟着卡片宽度左右跳，一屏三张卡片竖着
          对不齐；左边那段描述也会趁机漫到按钮底下。

          用 visibility 而不是写死宽度：按钮多宽就留多宽，将来文案变了不用回来
          调魔法数字。invisible 同时把它移出无障碍树，禁用按钮又不可聚焦，
          所以藏起来的按钮既点不到也不会被读屏念出来。
        */}
        <Button
          variant="outline"
          size="sm"
          onClick={onConfigure}
          disabled={busy || !configurable}
          className={configurable ? undefined : "invisible"}
        >
          <Pencil className="size-3.5" />
          配置
        </Button>
        <Switch
          checked={plugin.enabled}
          onCheckedChange={onToggle}
          disabled={busy}
          title={pendingRestart ? "改动需要重启应用才生效" : plugin.enabled ? "已启用" : "已关闭"}
        />
      </div>

      {plugin.error ? (
        <p className="mt-2 flex items-start gap-1.5 text-[11px] text-danger">
          <AlertCircle className="mt-px size-3.5 shrink-0" />
          <span className="min-w-0 break-words">{plugin.error}</span>
        </p>
      ) : null}
      {!plugin.error && pendingRestart ? (
        // 重启入口只有文字链接、没有按钮：整张卡片是一行的，每行挂个实心重启按钮
        // 会让人分不清管的是哪一行，而点错的代价是整个应用重启。真要重启时
        // 仍然走弹窗二次确认。
        <p className="mt-2 flex items-start gap-1.5 text-[11px] text-muted">
          <AlertCircle className="mt-px size-3.5 shrink-0" />
          <span className="min-w-0 break-words">
            {plugin.enabled ? "重启后生效。" : "重启后关闭。"}
            <button
              type="button"
              className="whitespace-nowrap text-accent underline-offset-2 hover:underline"
              onClick={onRequestRestart}
            >
              重启
            </button>
          </span>
        </p>
      ) : null}
      {!plugin.error && overridden.length > 0 ? (
        <p className="mt-2 flex items-start gap-1.5 text-[11px] text-muted">
          <AlertCircle className="mt-px size-3.5 shrink-0" />
          <span className="min-w-0 break-words">
            当前工作区的 .nono-agent/settings.json 覆盖了 {overridden.join("、")}，这里显示的是工作区的值。删掉那段即可回到全局配置。
          </span>
        </p>
      ) : null}
      {!plugin.error && needsKey ? (
        <p className="mt-2 text-[11px] text-muted">还差一个 {plugin.api_key.label}，填了工具才会真正生效。</p>
      ) : null}
      {/* pendingRestart 时不能说「已在本次对话中可用」：插件一开，工具确实已经
          进表了，但它们背后要挂的东西还没启动，这时候宣称可用是在骗人。 */}
      {!plugin.error && plugin.enabled && !needsKey && !pendingRestart && plugin.tools.length > 0 ? (
        <p className="mt-2 flex items-center gap-1.5 text-[11px] text-muted">
          <Check className="size-3.5 shrink-0" />
          已在本次对话中可用
        </p>
      ) : null}
    </div>
  );
}

function SourceBadge({ source }: { source: ToolPluginInfo["source"] }) {
  const label = source === "bundled" ? "内置" : source === "user" ? "用户" : "项目";
  return <span className="shrink-0 rounded-md bg-card px-1.5 py-0.5 text-[10px] text-faint">{label}</span>;
}

/**
 * 详情弹窗里的启停状态。
 *
 * 弹窗里**没有**启停开关了（只有一个「关闭」表示关窗口），所以这里是用户唯一
 * 能看到「现在到底开着没」的地方 —— 不说的话，他只能关掉弹窗去卡片上看。
 *
 * 「待重启」要单独一种说法：配置改了但没重启，开关显示的是他想要的，不是现状。
 */
function StatusTag({ enabled, pendingRestart }: { enabled: boolean; pendingRestart: boolean }) {
  if (pendingRestart) {
    return <span className="text-accent">{enabled ? "已开启 · 待重启生效" : "已关闭 · 待重启生效"}</span>;
  }
  return enabled ? "已启用" : "已关闭";
}

/**
 * 插件详情弹窗：描述、示例句、提供的工具、版本出处。
 *
 * 列表里那一行是给「扫一眼谁开着谁关着」的，看不清插件到底能干什么；点标题进
 * 这里是给「刚装上、想知道能拿它做什么」的。示例句可以一键复制 —— 让用户自己
 * 去猜怎么开口，是插件被发现率低最常见的原因。
 *
 * 同样不认识具体插件：示例句来自清单的 ``examples``，工具名来自运行时状态，
 * 都缺省就不渲染对应的小节。
 */
function PluginDetailDialog({
  plugin,
  onClose,
  onConfigure,
}: {
  plugin: ToolPluginInfo;
  onClose: () => void;
  onConfigure: () => void;
}) {
  // 捕获阶段拦 Esc：设置面板自己在冒泡阶段监听，不抢在前面的话一次按键会把
  // 弹窗和整个设置面板一起带走。
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      onClose();
    }
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  // ?? [] 与下面 project_owned 同理：前端走 vite HMR，sidecar 还是老版本时新字段
  // 会整个不存在。与其让整个插件面板白屏，不如先把这一节藏掉。
  const examples = plugin.examples ?? [];
  const configurable = plugin.fields.length > 0 || plugin.api_key.required;

  async function copy(text: string) {
    try {
      await writeClipboardText(text);
      toast("已复制");
    } catch (err) {
      toast(err instanceof Error ? err.message : "复制失败");
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/30 p-6"
      onPointerDown={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="plugin-detail-title"
        className="flex max-h-[85vh] w-full max-w-md flex-col rounded-2xl border border-line bg-bg p-5 shadow-[0_16px_48px_var(--shadow-color-strong)]"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-card text-muted">
            <Puzzle className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h2
              id="plugin-detail-title"
              className="flex flex-wrap items-center gap-2 text-[15px] font-medium"
            >
              <span className="truncate">{plugin.id}</span>
              <SourceBadge source={plugin.source} />
            </h2>
            <p className="mt-0.5 text-[11px] text-faint">
              版本 {plugin.version}
              <span className="mx-1.5 text-faint/60">·</span>
              <StatusTag enabled={plugin.enabled} pendingRestart={plugin.needs_restart && !plugin.live} />
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="-mr-1 -mt-1 shrink-0 rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-ink"
          >
            <X className="size-4" />
          </button>
        </div>

        {plugin.description ? (
          <p className="mt-4 text-[13px] leading-6 text-muted">{plugin.description}</p>
        ) : null}

        <div className="mt-4 min-h-0 flex-1 space-y-5 overflow-auto pr-1">
          {examples.length > 0 ? (
            <section>
              <h3 className="text-[13px] font-medium text-ink">可以这么问</h3>
              <div className="mt-2 space-y-1.5">
                {examples.map((example) => (
                  <div
                    key={example}
                    className="group flex items-start gap-2 rounded-xl border border-line px-3 py-2.5"
                  >
                    <p className="min-w-0 flex-1 text-[13px] leading-5">{example}</p>
                    {/* 复制按钮默认藏在 hover/focus 里：一列复制图标会让例子本身
                        变成一排按钮，反而更难扫读。 */}
                    <button
                      type="button"
                      onClick={() => void copy(example)}
                      aria-label="复制"
                      className="shrink-0 rounded-md p-1 text-faint opacity-0 transition-opacity hover:bg-hover hover:text-ink focus-visible:opacity-100 focus-visible:outline-none group-hover:opacity-100"
                    >
                      <Copy className="size-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {plugin.tools.length > 0 ? (
            <section>
              <h3 className="text-[13px] font-medium text-ink">提供的工具</h3>
              <div className="mt-2 space-y-1">
                {plugin.tools.map((name) => (
                  <div
                    key={name}
                    className="flex items-center gap-2 rounded-xl bg-sunken px-3 py-2"
                  >
                    <Terminal className="size-3.5 shrink-0 text-faint" />
                    <span className="truncate font-mono text-[12px]">{name}</span>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {plugin.error ? (
            <section>
              <h3 className="text-[13px] font-medium text-ink">状态</h3>
              <p className="mt-2 flex items-start gap-1.5 text-[12px] leading-5 text-danger">
                <AlertCircle className="mt-px size-3.5 shrink-0" />
                <span className="min-w-0 break-words">{plugin.error}</span>
              </p>
            </section>
          ) : null}
        </div>

        {/* 底部只剩「关闭」和「配置」。

            这里**刻意不放启停开关**：卡片上那一行的开关才是唯一的启停入口，
            弹窗里再来一个同名按钮，用户看到「关闭」会以为是在关插件而不是
            关窗口 —— 而插件列表里那行开关就开在眼前，多一个入口纯属自找歧义。
            想知道现在开没开，头部那一行状态标签说得很清楚。 */}
        <div className="mt-5 flex items-center justify-end gap-2 border-t border-line pt-4">
          <Button variant="outline" size="sm" onClick={onClose}>
            关闭
          </Button>
          {configurable ? (
            <Button size="sm" onClick={onConfigure}>
              <Pencil className="size-3.5" />
              配置
            </Button>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function PluginEditor({
  plugin,
  busy,
  error,
  onBack,
  onSave,
  onSaveKey,
}: {
  plugin: ToolPluginInfo;
  busy: boolean;
  error: string | null;
  onBack: () => void;
  onSave: (options: Record<string, unknown>) => Promise<void>;
  onSaveKey: (apiKey: string) => Promise<void>;
}) {
  // 初值来自服务端解析好的现值（用户配置优先，缺省回落清单 defaults）
  const [values, setValues] = useState<Record<string, string>>(() => seedValues(plugin));
  const [keyDraft, setKeyDraft] = useState("");
  const [confirmClearKey, setConfirmClearKey] = useState(false);

  // 切到另一个插件时重新取初值
  useEffect(() => {
    setValues(seedValues(plugin));
    setKeyDraft("");
  }, [plugin]);

  const dirty = useMemo(
    () => Object.keys(values).some((key) => values[key] !== initialValue(plugin, key)),
    [plugin, values],
  );

  function setField(field: ToolPluginField, next: string) {
    setValues((prev) => ({ ...prev, [field.key]: next }));
  }

  async function submitKey() {
    const trimmed = keyDraft.trim();
    await onSaveKey(trimmed);
    setKeyDraft("");
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <p className="text-[13px] text-muted">{plugin.description || plugin.id}</p>

      <div className="mt-4 min-h-0 flex-1 space-y-5 overflow-auto pr-1">
        {plugin.api_key.required ? (
          <section>
            <div className="flex items-center gap-2 text-[13px] font-medium text-ink">
              <KeyRound className="size-3.5 text-muted" />
              {plugin.api_key.label}
              {plugin.api_key.configured ? (
                <span className="rounded-md bg-card px-1.5 py-0.5 text-[10px] text-muted">已配置</span>
              ) : null}
            </div>
            {plugin.api_key.hint ? <p className="mt-1 text-[11px] text-muted">{plugin.api_key.hint}</p> : null}
            <div className="mt-2 flex items-center gap-2">
              <Input
                type="password"
                value={keyDraft}
                placeholder={plugin.api_key.configured ? "留空表示不修改" : plugin.api_key.placeholder || plugin.api_key.env}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setKeyDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && keyDraft.trim()) void submitKey();
                }}
              />
              <Button
                size="sm"
                className="shrink-0"
                disabled={busy || !keyDraft.trim()}
                onClick={() => void submitKey()}
              >
                {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
                {keyDraft.trim() ? "保存密钥" : "输入后保存"}
              </Button>
              {plugin.api_key.configured ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="shrink-0 text-danger"
                  disabled={busy}
                  onClick={() => setConfirmClearKey(true)}
                >
                  清除
                </Button>
              ) : null}
            </div>
            <p className="mt-1.5 font-mono text-[11px] text-faint">
              写入 {plugin.api_key.env}，不回显、也不会进项目配置文件。
            </p>
          </section>
        ) : null}

        {plugin.fields.length > 0 ? (
          <section className="space-y-4">
            <h3 className="text-[13px] font-medium text-ink">参数</h3>
            {plugin.fields.map((field) => (
              <FieldControl key={field.key} field={field} value={values[field.key] ?? ""} onChange={(next) => setField(field, next)} />
            ))}
          </section>
        ) : null}

        {error ? <p className="text-xs text-danger">{error}</p> : null}
      </div>

      <div className="mt-4 flex items-center justify-end gap-2">
        <Button variant="outline" size="sm" disabled={busy} onClick={onBack}>
          返回
        </Button>
        <Button size="sm" disabled={busy || !dirty} onClick={() => void onSave(values)}>
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
          保存参数
        </Button>
      </div>

      <ConfirmDialog
        open={confirmClearKey}
        title={`清除 ${plugin.api_key.label}？`}
        description="凭据文件里的这一条会被置空，插件会回到未配置状态。"
        confirmLabel="清除"
        danger
        onCancel={() => setConfirmClearKey(false)}
        onConfirm={() => {
          setConfirmClearKey(false);
          void onSaveKey("");
        }}
      />
    </div>
  );
}

/** 按字段类型渲染一个控件。新增控件类型只需要动这里。 */
function FieldControl({
  field,
  value,
  onChange,
}: {
  field: ToolPluginField;
  value: string;
  onChange: (next: string) => void;
}) {
  if (field.type === "enum") {
    return (
      <EnumControl field={field} value={value} onChange={onChange} />
    );
  }
  return (
    <div>
      <FieldLabelInline label={field.label} hint={field.hint} />
      <Input
        type={field.type === "number" ? "number" : field.type === "password" ? "password" : "text"}
        value={value}
        placeholder={field.placeholder}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function EnumControl({
  field,
  value,
  onChange,
}: {
  field: ToolPluginField;
  value: string;
  onChange: (next: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = field.choices.find((item) => item.value === value) ?? field.choices[0];
  return (
    <div>
      <FieldLabelInline label={field.label} hint={field.hint} />
      <Dropdown
        open={open}
        onOpenChange={setOpen}
        matchTriggerWidth
        trigger={
          <FieldSelectButton
            open={open}
            label={active?.label ?? value ?? "—"}
            onClick={() => setOpen((prev) => !prev)}
          />
        }
      >
        {field.choices.map((item) => (
          <DropdownItem
            key={item.value}
            selected={item.value === value}
            onClick={() => {
              onChange(item.value);
              setOpen(false);
            }}
          >
            {item.label}
          </DropdownItem>
        ))}
      </Dropdown>
    </div>
  );
}

function FieldLabelInline({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="mb-1">
      <label className="block text-xs text-muted">{label}</label>
      {hint ? <p className="mt-0.5 text-[11px] text-faint">{hint}</p> : null}
    </div>
  );
}

function initialValue(plugin: ToolPluginInfo, key: string): string {
  const field = plugin.fields.find((item) => item.key === key);
  if (!field || field.value === null || field.value === undefined) return "";
  return String(field.value);
}

function seedValues(plugin: ToolPluginInfo): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of plugin.fields) out[field.key] = initialValue(plugin, field.key);
  return out;
}
