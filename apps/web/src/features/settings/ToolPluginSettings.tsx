import { AlertCircle, Check, KeyRound, Loader2, Pencil, Puzzle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ToolPluginField, ToolPluginInfo } from "@nono/protocol";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dropdown, DropdownItem, FieldSelectButton } from "@/components/ui/dropdown";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useChatStore } from "@/store/chat";

/**
 * 工具插件的配置面板。
 *
 * **这一层不认识任何具体插件**：字段、控件类型、默认值全部来自服务端按插件
 * 清单生成的 schema。要支持一个新插件，在它的 plugin.json 里加一段 `config`
 * 就行，前端不用改 —— 否则每加一个厂商就要在这里加一段 if。
 */
export function ToolPluginSettings({ onViewChange }: { onViewChange?: (title: string | null) => void }) {
  const plugins = useChatStore((state) => state.toolPlugins);
  const credentialsPath = useChatStore((state) => state.toolPluginCredentialsPath);
  const refresh = useChatStore((state) => state.refreshToolPlugins);
  const save = useChatStore((state) => state.saveToolPlugin);
  const saveKey = useChatStore((state) => state.saveToolPluginApiKey);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const current = plugins.find((item) => item.id === editing) ?? null;

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
            />
          ))
        )}
      </div>

      {error ? <p className="mt-3 text-xs text-danger">{error}</p> : null}

      <div className="mt-4 flex items-center justify-between gap-3">
        <p className="truncate font-mono text-[11px] text-faint" title={credentialsPath ?? undefined}>
          密钥存放在 {credentialsPath || "~/.nono-agent/credentials"}
        </p>
      </div>
    </div>
  );
}

function PluginCard({
  plugin,
  busy,
  onToggle,
  onConfigure,
}: {
  plugin: ToolPluginInfo;
  busy: boolean;
  onToggle: (next: boolean) => void;
  onConfigure: () => void;
}) {
  // 有字段或要填密钥的插件才给"配置"入口；只有一个开关的插件直接就地开关。
  const configurable = plugin.fields.length > 0 || plugin.api_key.required;
  const needsKey = plugin.api_key.required && !plugin.api_key.configured;

  return (
    <div className="rounded-2xl border border-line bg-bg px-4 py-3">
      <div className="flex items-center gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-card text-muted">
          <Puzzle className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{plugin.id}</span>
            <SourceBadge source={plugin.source} />
          </div>
          <div className="mt-0.5 truncate text-[11px] text-faint">
            {plugin.tools.length > 0 ? plugin.tools.join(" · ") : plugin.description || "尚未提供工具"}
          </div>
        </div>
        {configurable ? (
          <Button variant="outline" size="sm" onClick={onConfigure} disabled={busy}>
            <Pencil className="size-3.5" />
            配置
          </Button>
        ) : null}
        <Switch checked={plugin.enabled} onCheckedChange={onToggle} disabled={busy} title={plugin.enabled ? "已启用" : "已关闭"} />
      </div>

      {plugin.error ? (
        <p className="mt-2 flex items-start gap-1.5 text-[11px] text-danger">
          <AlertCircle className="mt-px size-3.5 shrink-0" />
          <span className="min-w-0 break-words">{plugin.error}</span>
        </p>
      ) : null}
      {!plugin.error && needsKey ? (
        <p className="mt-2 text-[11px] text-muted">还差一个 {plugin.api_key.label}，填了工具才会真正生效。</p>
      ) : null}
      {!plugin.error && plugin.enabled && !needsKey && plugin.tools.length > 0 ? (
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
