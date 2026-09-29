import { Check, Code2, Copy, Download, MessageSquarePlus, MoreHorizontal } from "lucide-react";
import { useEffect, useState } from "react";

import { DiagramLightbox } from "@/components/DiagramLightbox";
import { Dropdown, DropdownItem } from "@/components/ui/dropdown";
import { encodeImages } from "@/lib/images";
import { downloadBlob, svgToPngBlob } from "@/lib/svg-png";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

const FILENAME = { mermaid: "mermaid.png", svg: "diagram.png" } as const;

/** 图表还没画出来时先占住卡片外框。最终高度仍由内容决定，滚动由列表的尺寸监听补齐。 */
export function FigurePlaceholder({ label }: { label: string }) {
  return (
    <div className="relative my-3 overflow-hidden rounded-lg border border-line bg-white" aria-busy="true">
      <div className="flex min-h-40 items-center justify-center px-3 pt-11 pb-3 text-[12px] text-faint">{label}</div>
    </div>
  );
}

const actionClass =
  "inline-flex h-7 cursor-pointer items-center gap-1 rounded-lg border border-line bg-white px-2 text-[12px] text-muted shadow-[0_4px_14px_rgba(15,17,21,0.06)] hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-ink/15 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-40";

/** Mermaid / SVG 图块：复制源码、下载 PNG，以及查看代码、加进当前对话。 */
export function FigureCard({
  svg,
  source,
  kind,
  error,
  detail,
}: {
  svg: string | null;
  source: string;
  kind: "mermaid" | "svg";
  error?: string;
  detail?: string;
}) {
  const [open, setOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const filename = FILENAME[kind];
  const frameClassName = kind === "svg" ? "svg-figure" : "mermaid-diagram";
  const zoomClassName = kind === "svg" ? "svg-zoom" : "mermaid-diagram";
  const showingCode = !svg || showCode;

  useEffect(() => {
    if (!copied && !notice) return;
    const timer = window.setTimeout(() => {
      setCopied(false);
      setNotice(null);
    }, 1600);
    return () => window.clearTimeout(timer);
  }, [copied, notice]);

  async function exportPng() {
    if (!svg) throw new Error("无法导出图片");
    return svgToPngBlob(svg);
  }

  return (
    <div className="relative my-3 overflow-hidden rounded-lg border border-line bg-white">
      <div className="absolute top-2 right-2 z-10 flex max-w-[calc(100%-1rem)] flex-wrap justify-end gap-1.5 select-none">
        <button
          type="button"
          className={actionClass}
          title={copied ? "已复制" : "复制代码"}
          onClick={() => {
            void navigator.clipboard.writeText(source).then(() => setCopied(true)).catch(() => setCopied(false));
          }}
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? "已复制" : "复制代码"}
        </button>
        {svg ? (
          <button
            type="button"
            className={actionClass}
            title="下载图片"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void exportPng()
                .then((blob) => downloadBlob(blob, filename))
                .catch(() => setNotice("导出失败"))
                .finally(() => setBusy(false));
            }}
          >
            <Download className="size-3.5" />
            下载图片
          </button>
        ) : null}
        {notice ? (
          <span className={cn(actionClass, "pointer-events-none")}>{notice}</span>
        ) : svg ? (
          <Dropdown
            open={menuOpen}
            onOpenChange={setMenuOpen}
            align="right"
            trigger={
              <button
                type="button"
                className={cn(actionClass, "px-1.5")}
                aria-label="更多"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                title="更多"
                onClick={() => setMenuOpen((value) => !value)}
              >
                <MoreHorizontal className="size-3.5" />
              </button>
            }
          >
            <DropdownItem
              disabled={busy}
              onClick={() => {
                setMenuOpen(false);
                setBusy(true);
                void exportPng()
                  .then(async (blob) => {
                    const file = new File([blob], filename, { type: "image/png" });
                    const images = await encodeImages([file]);
                    useChatStore.getState().addAttachments(images);
                    setNotice("已添加到对话");
                  })
                  .catch(() => setNotice("导出失败"))
                  .finally(() => setBusy(false));
              }}
            >
              <MessageSquarePlus className="mt-0.5 size-3.5 shrink-0 text-muted" />
              <span className="text-[13px]">添加到对话</span>
            </DropdownItem>
            <DropdownItem
              onClick={() => {
                setMenuOpen(false);
                setShowCode((value) => !value);
              }}
            >
              <Code2 className="mt-0.5 size-3.5 shrink-0 text-muted" />
              <span className="text-[13px]">{showCode ? "查看图表" : "查看代码"}</span>
            </DropdownItem>
          </Dropdown>
        ) : null}
      </div>

      <div className="pt-11">
        {error ? (
          <p className="mx-3 mb-2 rounded-md bg-[#fdf6e3] px-2.5 py-1.5 text-[12px] text-[#7a5b12]" title={detail}>
            {error}
          </p>
        ) : null}
        {showingCode ? (
          <pre className="max-h-96 overflow-auto px-3 pb-3 font-mono text-[12.5px] leading-6 whitespace-pre">{source}</pre>
        ) : svg ? (
          <div className="px-3 pb-3">
            <button
              type="button"
              className={`${frameClassName} block w-full overflow-auto text-left`}
              title="点击放大"
              onClick={() => setOpen(true)}
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          </div>
        ) : null}
      </div>
      {open && svg ? <DiagramLightbox svg={svg} onClose={() => setOpen(false)} frameClassName={zoomClassName} /> : null}
    </div>
  );
}
