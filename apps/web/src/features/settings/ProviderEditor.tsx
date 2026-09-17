import type { CatalogProvider, ProviderUpsert } from "@nono/protocol";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { fetchRemoteModels } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dropdown, DropdownItem, FieldSelectButton } from "@/components/ui/dropdown";
import { FieldLabel, Input } from "@/components/ui/input";
import { ADAPTER_LABELS, PROVIDER_ID_PATTERN, titleFromId } from "@/features/settings/presets";
import { formatTokenSize, parseTokenSize } from "@/lib/format";
import { cn } from "@/lib/utils";

type ModelDraft = {
  key: string;
  id: string;
  name: string;
  context_window: string;
  max_tokens: string;
};

export type ProviderDraft = {
  mode: "create" | "edit";
  id: string;
  display_name: string;
  api: string;
  base_url: string;
  has_key: boolean;
  extra_headers: string;
  extra_body: string;
  models: ModelDraft[];
};

type ProviderEditorProps = {
  draft: ProviderDraft;
  adapters: string[];
  existingIds: string[];
  saving: boolean;
  onCancel: () => void;
  onSave: (body: ProviderUpsert) => Promise<void>;
  onDelete?: () => Promise<void>;
};

function nextKey(): string {
  return crypto.randomUUID();
}

function emptyModel(): ModelDraft {
  return {
    key: nextKey(),
    id: "",
    name: "",
    context_window: "256K",
    max_tokens: "32K",
  };
}

function jsonObjectText(value: Record<string, unknown> | undefined): string {
  if (!value || Object.keys(value).length === 0) return "";
  return JSON.stringify(value, null, 2);
}

function parseJsonObject(raw: string, label: string): Record<string, unknown> {
  const text = raw.trim();
  if (!text) return {};
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`${label} 不是合法 JSON`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error(`${label} 必须是 JSON 对象，例如 {"k":"v"}`);
  }
  return data as Record<string, unknown>;
}

function parseExtraHeaders(raw: string): Record<string, string> {
  const obj = parseJsonObject(raw, "扩展请求头");
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(obj)) {
    if (!key.trim() || value == null) continue;
    if (typeof value === "object") {
      throw new Error("扩展请求头的值必须是字符串");
    }
    headers[key] = String(value);
  }
  return headers;
}

export function draftFromProvider(provider: CatalogProvider): ProviderDraft {
  const models = provider.models.length
    ? provider.models.map((item) => ({
        key: nextKey(),
        id: item.id,
        name: item.name ?? "",
        context_window: formatTokenSize(item.context_window) || "256K",
        max_tokens: formatTokenSize(item.max_tokens) || "32K",
      }))
    : [emptyModel()];
  return {
    mode: "edit",
    id: provider.id,
    display_name: provider.display_name,
    api: provider.api,
    base_url: provider.base_url ?? "",
    has_key: provider.has_key,
    extra_headers: jsonObjectText(provider.extra_headers),
    extra_body: jsonObjectText(provider.extra_body),
    models,
  };
}

export function emptyDraft(api: string, preset?: Partial<ProviderDraft>): ProviderDraft {
  const { models, ...rest } = preset ?? {};
  return {
    id: "",
    display_name: "",
    api,
    base_url: "",
    has_key: false,
    extra_headers: "",
    extra_body: "",
    ...rest,
    mode: "create",
    models: models?.length ? models : [emptyModel()],
  };
}

function fieldClass(invalid?: boolean) {
  return cn(
    "h-10 border-transparent bg-card",
    invalid && "border-danger/40 bg-danger/5",
  );
}

function jsonFieldClass() {
  return cn(
    "min-h-[88px] w-full resize-y rounded-lg border border-transparent bg-card px-2.5 py-2 font-mono text-[12px] leading-5 text-ink placeholder:text-faint focus:border-line-strong focus:outline-none",
  );
}

export function ProviderEditor({
  draft,
  adapters,
  existingIds,
  saving,
  onCancel,
  onSave,
  onDelete,
}: ProviderEditorProps) {
  const [id, setId] = useState(draft.id);
  const [displayName, setDisplayName] = useState(draft.display_name);
  const [nameTouched, setNameTouched] = useState(Boolean(draft.display_name));
  const [api, setApi] = useState(draft.api);
  const [apiOpen, setApiOpen] = useState(false);
  const [baseUrl, setBaseUrl] = useState(draft.base_url);
  const [apiKey, setApiKey] = useState("");
  const [extraHeaders, setExtraHeaders] = useState(draft.extra_headers);
  const [extraBody, setExtraBody] = useState(draft.extra_body);
  const [models, setModels] = useState<ModelDraft[]>(draft.models);
  const [formError, setFormError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const creating = draft.mode === "create";
  const idInvalid = creating && id.trim().length > 0 && !PROVIDER_ID_PATTERN.test(id.trim());
  const idTaken = creating && existingIds.includes(id.trim());

  const canSubmit = useMemo(() => {
    if (!id.trim()) return false;
    if (creating && !PROVIDER_ID_PATTERN.test(id.trim())) return false;
    if (creating && existingIds.includes(id.trim())) return false;
    return models.some((item) => item.id.trim());
  }, [creating, existingIds, id, models]);

  function patchModel(key: string, patch: Partial<ModelDraft>) {
    setModels((current) => current.map((item) => (item.key === key ? { ...item, ...patch } : item)));
  }

  async function submit() {
    setFormError(null);
    const pid = id.trim();
    if (creating && !PROVIDER_ID_PATTERN.test(pid)) {
      setFormError("Provider ID 需以小写字母开头，只能包含小写字母、数字、连字符和下划线。");
      return;
    }
    let headers: Record<string, string>;
    let bodyExtra: Record<string, unknown>;
    try {
      headers = parseExtraHeaders(extraHeaders);
      bodyExtra = parseJsonObject(extraBody, "扩展请求体");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "扩展配置无效");
      return;
    }
    const parsed: ProviderUpsert["models"] = [];
    for (const item of models) {
      const mid = item.id.trim();
      if (!mid) continue;
      if (item.context_window.trim() && parseTokenSize(item.context_window) == null) {
        setFormError(`模型 ${mid} 的上下文窗口无效，请输入正整数或 256K / 1M。`);
        return;
      }
      if (item.max_tokens.trim() && parseTokenSize(item.max_tokens) == null) {
        setFormError(`模型 ${mid} 的最大输出 token 无效，请输入正整数或 32K / 1M。`);
        return;
      }
      parsed.push({
        id: mid,
        name: item.name.trim() || undefined,
        context_window: parseTokenSize(item.context_window),
        max_tokens: parseTokenSize(item.max_tokens),
      });
    }
    if (!parsed.length) {
      setFormError("至少添加一个模型 ID。");
      return;
    }
    try {
      await onSave({
        id: pid,
        display_name: displayName.trim() || titleFromId(pid) || pid,
        api,
        base_url: baseUrl.trim() || null,
        api_key: apiKey.trim() || null,
        extra_headers: headers,
        extra_body: bodyExtra,
        models: parsed,
      });
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "保存失败");
    }
  }

  async function fetchModels() {
    setFormError(null);
    setFetching(true);
    try {
      const remote = await fetchRemoteModels({
        base_url: baseUrl.trim(),
        api,
        api_key: apiKey.trim() || null,
        provider_id: creating ? null : draft.id,
      });
      if (!remote.length) {
        setFormError("接口没有返回可用模型，请手动添加。");
        return;
      }
      setModels((current) => {
        const kept = current.filter((item) => item.id.trim());
        const have = new Set(kept.map((item) => item.id.trim()));
        const incoming = remote
          .filter((item) => !have.has(item.id))
          .map((item) => ({
            key: nextKey(),
            id: item.id,
            name: item.name && item.name !== item.id ? item.name : "",
            context_window: "256K",
            max_tokens: "32K",
          }));
        const next = [...kept, ...incoming];
        return next.length ? next : [emptyModel()];
      });
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "获取模型失败");
    } finally {
      setFetching(false);
    }
  }

  async function remove() {
    if (!onDelete) return;
    if (!window.confirm(`删除提供方「${draft.display_name || draft.id}」？此操作不可撤销。`)) return;
    setDeleting(true);
    setFormError(null);
    try {
      await onDelete();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-auto pr-1">
        <div className="rounded-2xl border border-line bg-sidebar/60 p-5">
          <h3 className="text-[15px] font-medium">{creating ? "自定义提供方" : "编辑提供方"}</h3>

          <div className="mt-4">
            <FieldLabel htmlFor="provider-id">Provider ID</FieldLabel>
            <Input
              id="provider-id"
              value={id}
              disabled={!creating}
              placeholder="acme-gateway"
              className={fieldClass(idInvalid || idTaken)}
              onChange={(event) => {
                const next = event.target.value;
                setId(next);
                if (!nameTouched) setDisplayName(titleFromId(next));
              }}
            />
            <p className="mt-1.5 text-[11px] leading-5 text-muted">
              以小写字母开头的标识，在目录中唯一标识该提供方，并用于派生凭据名。
            </p>
            {idInvalid ? (
              <p className="mt-1 text-[11px] text-danger">只能使用小写字母、数字、连字符和下划线。</p>
            ) : null}
            {idTaken ? <p className="mt-1 text-[11px] text-danger">该 Provider ID 已存在。</p> : null}
          </div>

          <div className="mt-4">
            <FieldLabel htmlFor="provider-name">显示名称</FieldLabel>
            <Input
              id="provider-name"
              value={displayName}
              placeholder="显示名称"
              className={fieldClass()}
              onChange={(event) => {
                setNameTouched(true);
                setDisplayName(event.target.value);
              }}
            />
          </div>

          <div className="mt-4">
            <FieldLabel htmlFor="provider-url">API 地址</FieldLabel>
            <Input
              id="provider-url"
              value={baseUrl}
              placeholder="https://gateway.example/v1"
              className={fieldClass()}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
          </div>

          <div className="mt-4">
            <FieldLabel htmlFor="provider-api">API 协议</FieldLabel>
            <Dropdown
              open={apiOpen}
              onOpenChange={setApiOpen}
              matchTriggerWidth
              trigger={
                <FieldSelectButton
                  id="provider-api"
                  open={apiOpen}
                  label={ADAPTER_LABELS[api] ?? api}
                  className={fieldClass()}
                  onClick={() => setApiOpen((value) => !value)}
                />
              }
            >
              {(adapters.length ? adapters : Object.keys(ADAPTER_LABELS)).map((item) => (
                <DropdownItem
                  key={item}
                  selected={item === api}
                  onClick={() => {
                    setApi(item);
                    setApiOpen(false);
                  }}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[12px] text-ink">
                      {ADAPTER_LABELS[item] ?? item}
                    </span>
                    <span className="mt-0.5 block truncate text-[11px] leading-4 text-faint">{item}</span>
                  </span>
                </DropdownItem>
              ))}
            </Dropdown>
          </div>

          <div className="mt-4">
            <FieldLabel htmlFor="provider-key">API 密钥</FieldLabel>
            <Input
              id="provider-key"
              type="password"
              value={apiKey}
              placeholder={draft.has_key ? "已保存，留空则保持不变" : "输入 API 密钥"}
              className={fieldClass()}
              autoComplete="off"
              onChange={(event) => setApiKey(event.target.value)}
            />
          </div>

          <div className="mt-4">
            <FieldLabel htmlFor="provider-headers">扩展请求头</FieldLabel>
            <textarea
              id="provider-headers"
              value={extraHeaders}
              placeholder={'{"X-Foo":"bar"}'}
              className={jsonFieldClass()}
              spellCheck={false}
              onChange={(event) => setExtraHeaders(event.target.value)}
            />
            <p className="mt-1.5 text-[11px] leading-5 text-muted">
              合并进每次请求的 HTTP 头，JSON 对象，可留空。
            </p>
          </div>

          <div className="mt-4">
            <FieldLabel htmlFor="provider-body">扩展请求体</FieldLabel>
            <textarea
              id="provider-body"
              value={extraBody}
              placeholder={'{"reasoning_split":true}'}
              className={jsonFieldClass()}
              spellCheck={false}
              onChange={(event) => setExtraBody(event.target.value)}
            />
            <p className="mt-1.5 text-[11px] leading-5 text-muted">
              深度合并进每次请求体，JSON 对象，可留空。
            </p>
          </div>

          <div className="mt-5">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-xs text-muted">模型目录</div>
              <button
                type="button"
                className="text-[12px] text-muted hover:text-ink disabled:opacity-40"
                disabled={fetching || !baseUrl.trim()}
                onClick={() => void fetchModels()}
              >
                {fetching ? "正在获取…" : "获取可用模型"}
              </button>
            </div>
            <div className="space-y-3">
              {models.map((item) => (
                <div key={item.key} className="rounded-xl border border-line bg-bg p-3">
                  <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
                    <Input
                      value={item.id}
                      placeholder="模型 ID"
                      className={fieldClass()}
                      onChange={(event) => patchModel(item.key, { id: event.target.value })}
                    />
                    <Input
                      value={item.name}
                      placeholder="显示名称"
                      className={fieldClass()}
                      onChange={(event) => patchModel(item.key, { name: event.target.value })}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-muted"
                      disabled={models.length <= 1}
                      onClick={() =>
                        setModels((current) =>
                          current.length <= 1 ? current : current.filter((row) => row.key !== item.key),
                        )
                      }
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <div>
                      <FieldLabel>上下文窗口</FieldLabel>
                      <Input
                        value={item.context_window}
                        placeholder="256K"
                        className={fieldClass()}
                        onChange={(event) => patchModel(item.key, { context_window: event.target.value })}
                      />
                    </div>
                    <div>
                      <FieldLabel>最大输出 token</FieldLabel>
                      <Input
                        value={item.max_tokens}
                        placeholder="32K"
                        className={fieldClass()}
                        onChange={(event) => patchModel(item.key, { max_tokens: event.target.value })}
                      />
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <Button variant="outline" size="sm" className="mt-3" onClick={() => setModels((current) => [...current, emptyModel()])}>
              <Plus className="size-3.5" />
              添加模型
            </Button>
          </div>
        </div>
      </div>

      {formError ? <p className="mt-3 text-xs text-danger">{formError}</p> : null}

      <div className="mt-4 flex items-center justify-between gap-2">
        <div>
          {onDelete ? (
            <Button variant="ghost" className="text-danger hover:bg-danger/10" disabled={saving || deleting} onClick={() => void remove()}>
              删除提供方
            </Button>
          ) : null}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onCancel} disabled={saving || deleting}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={saving || deleting || !canSubmit}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : null}
            {creating ? "创建提供方" : "保存"}
          </Button>
        </div>
      </div>
    </div>
  );
}
