import { Box, ChevronDown } from "lucide-react";
import { useState } from "react";

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
  const providers = catalog?.providers ?? [];
  const entries = providers.flatMap((provider) =>
    provider.models.map((item) => ({ provider, item })),
  );
  const current = entries.find((entry) => entry.item.id === model);
  const label = current?.item.name || current?.item.id || "选择模型";

  async function choose(id: string) {
    setOpen(false);
    await setModel(id);
  }

  const menu =
    entries.length === 0 ? (
      <div className="px-2.5 py-2 text-[12px] text-muted">还没有可用模型</div>
    ) : (
      providers.map((provider) => (
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
    );

  if (variant === "field") {
    return (
      <Dropdown
        open={open}
        onOpenChange={setOpen}
        matchTriggerWidth
        trigger={
          <FieldSelectButton
            id={id}
            open={open}
            label={label}
            title={label}
            onClick={() => setOpen((value) => !value)}
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
      onOpenChange={setOpen}
      align="right"
      placement="top"
      panelClassName="w-[240px]"
      trigger={
        <button
          type="button"
          className="flex min-w-0 max-w-[10rem] items-center gap-1 rounded-full px-2 py-1 text-[12px] text-muted hover:bg-hover"
          title={label}
          disabled={streaming}
          onClick={() => setOpen((value) => !value)}
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
