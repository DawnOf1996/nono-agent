import type { CatalogProvider } from "@nono/protocol";
import { Pencil, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { adapterLabel, PROVIDER_PRESETS, type ProviderPreset } from "@/features/settings/presets";
import { cn } from "@/lib/utils";

type ProviderListProps = {
  providers: CatalogProvider[];
  defaultProvider?: string | null;
  defaultModel?: string | null;
  onEdit: (provider: CatalogProvider) => void;
  onChooseDefault: (providerId: string, modelId: string) => void;
  onAddPreset: () => void;
  onAddCustom: () => void;
};

export function ProviderList({
  providers,
  defaultProvider,
  defaultModel,
  onEdit,
  onChooseDefault,
  onAddPreset,
  onAddCustom,
}: ProviderListProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div>
        <h3 className="text-[15px] font-medium">模型</h3>
        <p className="mt-1 text-[13px] text-muted">输入各提供方的 API 密钥即可使用其模型。</p>
      </div>

      <div className="mt-4 min-h-0 flex-1 space-y-2 overflow-auto pr-1">
        {providers.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">
            还没有提供方。先添加一个，再选模型开始对话。
          </div>
        ) : (
          providers.map((provider) => {
            const isDefault = defaultProvider === provider.id;
            return (
              <div key={provider.id} className="rounded-2xl border border-line bg-bg px-4 py-3">
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{provider.display_name}</span>
                      <span
                        className={cn(
                          "size-1.5 shrink-0 rounded-full",
                          provider.has_key ? "bg-emerald-500" : "bg-danger/70",
                        )}
                        title={provider.has_key ? "已配置密钥" : "缺少密钥"}
                      />
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-faint">
                      {provider.id} · {adapterLabel(provider.api)}
                      {provider.has_key ? "" : " · 缺密钥"}
                    </div>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => onEdit(provider)}>
                    <Pencil className="size-3.5" />
                    编辑
                  </Button>
                </div>
                {provider.models.length ? (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {provider.models.map((item) => {
                      const selected = isDefault && defaultModel === item.id;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          className={cn(
                            "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                            selected
                              ? "border-ink bg-ink text-white"
                              : "border-line text-muted hover:border-line-strong hover:text-ink",
                          )}
                          onClick={() => onChooseDefault(provider.id, item.id)}
                        >
                          {item.name || item.id}
                          {selected ? " · 默认" : ""}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <Button variant="outline" className="h-11 rounded-xl" onClick={onAddPreset}>
          <Plus className="size-4" />
          添加提供方
        </Button>
        <Button variant="outline" className="h-11 rounded-xl" onClick={onAddCustom}>
          <Plus className="size-4" />
          添加自定义提供方
        </Button>
      </div>
    </div>
  );
}

type PresetPickerProps = {
  existingIds: string[];
  onPick: (preset: ProviderPreset) => void;
  onCancel: () => void;
};

export function PresetPicker({ existingIds, onPick, onCancel }: PresetPickerProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div>
        <h3 className="text-[15px] font-medium">添加提供方</h3>
        <p className="mt-1 text-[13px] text-muted">选择常用供应商，预填协议和 API 地址，再补密钥与模型。</p>
      </div>
      <div className="mt-4 grid min-h-0 flex-1 grid-cols-2 content-start gap-2 overflow-auto pr-1">
        {PROVIDER_PRESETS.map((preset) => {
          const added = existingIds.includes(preset.id);
          return (
            <button
              key={preset.id}
              type="button"
              disabled={added}
              className="rounded-2xl border border-line px-4 py-3 text-left transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:opacity-45"
              onClick={() => onPick(preset)}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{preset.display_name}</span>
                {added ? <span className="text-[11px] text-faint">已添加</span> : null}
              </div>
              <div className="mt-1 text-[11px] text-muted">{preset.hint}</div>
              <div className="mt-1 font-mono text-[11px] text-faint">{adapterLabel(preset.api)}</div>
            </button>
          );
        })}
      </div>
      <div className="mt-4 flex justify-end">
        <Button variant="outline" onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  );
}
