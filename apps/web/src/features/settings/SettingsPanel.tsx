import { useState } from "react";

import { Button } from "@/components/ui/button";
import { FieldLabel, Input, Select } from "@/components/ui/input";
import { useChatStore } from "@/store/chat";

export function SettingsPanel() {
  const { settingsOpen, setSettingsOpen, catalog, upsertProvider, chooseDefault, setModel } =
    useChatStore();
  const [id, setId] = useState("custom");
  const [displayName, setDisplayName] = useState("");
  const [api, setApi] = useState(catalog?.adapters[0] ?? "openai_chat_completions");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelId, setModelId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  if (!settingsOpen) return null;

  async function onSave() {
    setSaving(true);
    setFormError(null);
    try {
      await upsertProvider({
        id,
        display_name: displayName || id,
        api,
        base_url: baseUrl || null,
        api_key: apiKey || null,
        models: [{ id: modelId }],
      });
      setApiKey("");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/55 p-6">
      <div className="max-h-[90vh] w-full max-w-2xl overflow-auto border border-line bg-ink p-5">
        <div className="flex items-start justify-between">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-copper">
              设置
            </div>
            <h2 className="mt-2 text-lg font-medium">模型</h2>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setSettingsOpen(false)}>
            关闭
          </Button>
        </div>

        <ul className="mt-4 space-y-2">
          {(catalog?.providers ?? []).length === 0 ? (
            <li className="text-sm text-muted">还没有模型。在下面添加一条供应商。</li>
          ) : (
            catalog?.providers.map((provider) => (
              <li key={provider.id} className="border border-line p-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm">{provider.display_name}</div>
                    <div className="font-mono text-[11px] text-muted">
                      {provider.id} · {provider.api}
                      {provider.has_key ? "" : " · 缺密钥"}
                    </div>
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {provider.models.map((item) => {
                    const selected =
                      catalog?.default?.provider === provider.id &&
                      catalog.default.model === item.id;
                    return (
                      <Button
                        key={item.id}
                        size="sm"
                        variant={selected ? "default" : "outline"}
                        onClick={() => {
                          setModel(item.id);
                          void chooseDefault(provider.id, item.id);
                        }}
                      >
                        {item.name || item.id}
                        {selected ? " · 默认" : ""}
                      </Button>
                    );
                  })}
                </div>
              </li>
            ))
          )}
        </ul>

        <div className="mt-6 border-t border-line pt-4">
          <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted">
            添加 / 更新
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div>
              <FieldLabel htmlFor="pid">供应商 ID</FieldLabel>
              <Input id="pid" value={id} onChange={(event) => setId(event.target.value)} />
            </div>
            <div>
              <FieldLabel htmlFor="pname">显示名</FieldLabel>
              <Input
                id="pname"
                value={displayName}
                placeholder="MiniMax"
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </div>
            <div className="col-span-2">
              <FieldLabel htmlFor="papi">协议</FieldLabel>
              <Select id="papi" value={api} onChange={(event) => setApi(event.target.value)}>
                {(catalog?.adapters ?? ["openai_chat_completions"]).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </Select>
            </div>
            <div className="col-span-2">
              <FieldLabel htmlFor="purl">Base URL</FieldLabel>
              <Input
                id="purl"
                value={baseUrl}
                placeholder="https://api.example.com/v1"
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </div>
            <div>
              <FieldLabel htmlFor="pmid">模型 ID</FieldLabel>
              <Input
                id="pmid"
                value={modelId}
                placeholder="MiniMax-M3"
                onChange={(event) => setModelId(event.target.value)}
              />
            </div>
            <div>
              <FieldLabel htmlFor="pkey">API Key</FieldLabel>
              <Input
                id="pkey"
                type="password"
                value={apiKey}
                placeholder="写入 credentials，不会出现在 YAML"
                onChange={(event) => setApiKey(event.target.value)}
              />
            </div>
          </div>
          {formError ? <p className="mt-2 text-xs text-danger">{formError}</p> : null}
          <div className="mt-4 flex justify-end">
            <Button onClick={() => void onSave()} disabled={saving || !id.trim() || !modelId.trim()}>
              保存
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
