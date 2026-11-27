import { Box, ChevronDown, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Dropdown, DropdownItem, FieldSelectButton } from "@/components/ui/dropdown";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

export function ModelSelect({
  variant = "composer",
  id,
}: {
  variant?: "composer" | "field";
  id?: string;
}) {
  const model = useChatStore((state) => state.model);
  const catalog = useChatStore((state) => state.catalog);
  const setModel = useChatStore((state) => state.setModel);
  const streaming = useChatStore((state) => state.streaming);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const searchable = variant === "composer";
  const providers = catalog?.providers ?? [];
  const entries = providers.flatMap((provider) =>
    provider.models.map((item) => ({ provider, item })),
  );
  const current = entries.find((entry) => entry.item.id === model);
  const label = current?.item.name || current?.item.id || "选择模型";

  // 名称和 id 都参与匹配，模型 id 往往比展示名更好记（gpt-5、deepseek-chat）。
  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return providers;
    return providers
      .map((provider) => ({
        ...provider,
        models: provider.models.filter(
          (item) =>
            (item.name ?? "").toLowerCase().includes(keyword) ||
            item.id.toLowerCase().includes(keyword),
        ),
      }))
      .filter((provider) => provider.models.length > 0);
  }, [providers, query]);
  const matched = visible.reduce((total, provider) => total + provider.models.length, 0);

  useEffect(() => {
    if (open && searchable) searchRef.current?.focus();
  }, [open, searchable]);

  function setPanel(next: boolean) {
    setOpen(next);
    if (!next) setQuery("");
  }

  async function choose(id: string) {
    setPanel(false);
    await setModel(id);
  }

  const search = searchable ? (
    <div className="sticky top-0 z-10 -mt-1 border-b border-line bg-bg p-2">
      <label className="flex h-8 items-center gap-2 rounded-lg bg-card px-2.5 text-muted">
        <Search className="size-3.5 shrink-0" />
        <input
          ref={searchRef}
          value={query}
          placeholder="搜索名称或 id"
          aria-label="搜索模型"
          className="h-full w-full bg-transparent text-[13px] text-ink outline-none placeholder:text-faint"
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
    </div>
  ) : null;

  const menu = (
    <>
      {search}
      {matched === 0 ? (
        <div className="px-2.5 py-2 text-[12px] text-muted">
          {entries.length === 0 ? "还没有可用模型" : "没有匹配的模型"}
        </div>
      ) : (
        visible.map((provider) => (
          <div key={provider.id}>
            <div className="px-2.5 pt-2 pb-1 text-[11px] text-faint">
              {provider.display_name}
              {provider.has_key ? "" : " · 缺密钥"}
            </div>
            {provider.models.map((item) => {
              const selected = item.id === model;
              const name = item.name || item.id;
              return (
                <DropdownItem
                  key={`${provider.id}:${item.id}`}
                  selected={selected}
                  onClick={() => void choose(item.id)}
                >
                  <Box className="mt-0.5 size-3.5 shrink-0 text-muted" />
                  <span className="min-w-0">
                    <span className="block truncate text-[12px] text-ink">{name}</span>
                    <span className="mt-0.5 block truncate text-[11px] leading-4 text-faint">
                      {item.id}
                    </span>
                  </span>
                </DropdownItem>
              );
            })}
          </div>
        ))
      )}
    </>
  );

  if (variant === "field") {
    return (
      <Dropdown
        open={open}
        onOpenChange={setPanel}
        matchTriggerWidth
        trigger={
          <FieldSelectButton
            id={id}
            open={open}
            label={label}
            title={label}
            onClick={() => setPanel(!open)}
          />
        }
      >
        {menu}
      </Dropdown>
    );
  }

  return (
    <Dropdown
      open={open}
      onOpenChange={setPanel}
      align="right"
      placement="top"
      panelClassName="w-[280px]"
      trigger={
        <button
          type="button"
          className="flex min-w-0 max-w-[10rem] items-center gap-1 rounded-full px-2 py-1 text-[12px] text-muted hover:bg-hover"
          title={label}
          disabled={streaming}
          onClick={() => setPanel(!open)}
        >
          <Box className="size-3.5 shrink-0" />
          <span className="truncate">{label}</span>
          <ChevronDown className={cn("size-3 shrink-0", open && "rotate-180")} />
        </button>
      }
    >
      {menu}
    </Dropdown>
  );
}
