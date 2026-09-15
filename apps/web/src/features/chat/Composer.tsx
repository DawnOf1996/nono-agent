import { ArrowUp, Paperclip, Plus, Square, X } from "lucide-react";
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from "react";

import { ImageLightbox } from "@/components/ImageLightbox";
import { Textarea } from "@/components/ui/textarea";
import { CommandPalette } from "@/features/chat/CommandPalette";
import { ModelSelect } from "@/features/chat/ModelSelect";
import { PermissionModeSelect } from "@/features/chat/PermissionModeSelect";
import { FALLBACK_COMMANDS } from "@/lib/commands";
import { formatDuration, formatTokens } from "@/lib/format";
import {
  ACCEPT_IMAGE,
  encodeImages,
  filesFromClipboard,
  filesFromList,
} from "@/lib/images";
import { useChatStore } from "@/store/chat";

export function Composer() {
  const {
    input,
    setInput,
    send,
    runCommand,
    abort,
    streaming,
    commands,
    error,
    usage,
    attachments,
    addAttachments,
    removeAttachment,
    setError,
  } = useChatStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const slashQuery = input.startsWith("/") ? input.slice(1).split(/\s/)[0] ?? "" : "";
  const paletteOpen = menuOpen || input.startsWith("/");
  const canSend = Boolean(input.trim() || attachments.length);

  useEffect(() => {
    if (!paletteOpen) return;
    function onPointer(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [paletteOpen]);

  async function ingestFiles(files: File[]) {
    if (!files.length || streaming) return;
    try {
      addAttachments(await encodeImages(files));
    } catch (err) {
      setError(err instanceof Error ? err.message : "添加图片失败");
    }
  }

  const ingestRef = useRef(ingestFiles);
  ingestRef.current = ingestFiles;

  useEffect(() => {
    function onWindowPaste(event: globalThis.ClipboardEvent) {
      const files = filesFromClipboard(event);
      if (!files.length) return;
      const target = event.target;
      if (target instanceof Node && composerRef.current?.contains(target)) return;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
      event.preventDefault();
      void ingestRef.current(files);
    }
    document.addEventListener("paste", onWindowPaste);
    return () => document.removeEventListener("paste", onWindowPaste);
  }, []);

  function onPaste(event: ClipboardEvent<HTMLElement>) {
    const files = filesFromClipboard(event);
    if (!files.length) return;
    event.preventDefault();
    void ingestFiles(files);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(false);
    void ingestFiles(filesFromList(event.dataTransfer.files));
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape" && paletteOpen) {
      event.preventDefault();
      setMenuOpen(false);
      if (input.startsWith("/")) setInput("");
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (event.repeat || streaming || !canSend) return;
      void send();
    }
  }

  return (
    <div className="px-6 pb-3">
      <div className="mx-auto max-w-[52rem]">
        {error ? <div className="mb-2 px-2 text-xs text-danger">{error}</div> : null}
        <div
          ref={composerRef}
          className={`rounded-[22px] border bg-bg px-3 pt-3 pb-2 shadow-[0_8px_24px_rgba(15,17,21,0.06)] ${
            dragOver ? "border-accent" : "border-line"
          }`}
          onPaste={onPaste}
          onDragOver={(event) => {
            if (![...event.dataTransfer.types].includes("Files")) return;
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          {attachments.length ? (
            <div className="mb-2 flex flex-wrap gap-2">
              {attachments.map((item) => (
                <div key={item.id} className="relative">
                  <button
                    type="button"
                    className="block cursor-zoom-in"
                    title="查看大图"
                    onClick={() => setPreview(item.preview)}
                  >
                    <img
                      src={item.preview}
                      alt={item.name}
                      className="h-16 w-16 rounded-xl border border-line object-cover"
                    />
                  </button>
                  <button
                    type="button"
                    className="absolute -top-1.5 -right-1.5 z-10 flex size-5 items-center justify-center rounded-full border border-line bg-bg text-muted hover:bg-hover hover:text-ink"
                    title="移除图片"
                    disabled={streaming}
                    onClick={(event) => {
                      event.stopPropagation();
                      removeAttachment(item.id);
                    }}
                  >
                    <X className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          <Textarea
            rows={2}
            value={input}
            placeholder="描述你想做的事，可粘贴截图或添加图片"
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <div className="mt-2 flex items-center justify-between gap-2">
            <div ref={menuRef} className="relative flex min-w-0 items-center gap-1">
              <CommandPalette
                open={paletteOpen}
                query={slashQuery}
                commands={commands.length ? commands : FALLBACK_COMMANDS}
                onPick={(command) => {
                  setMenuOpen(false);
                  void runCommand(`/${command.name}`);
                }}
              />
              <button
                type="button"
                className="flex size-8 items-center justify-center rounded-full text-muted hover:bg-hover hover:text-ink"
                title="指令"
                disabled={streaming}
                onClick={() => setMenuOpen((value) => !value)}
              >
                <Plus className="size-4" />
              </button>
              <input
                ref={fileRef}
                type="file"
                accept={ACCEPT_IMAGE}
                multiple
                className="hidden"
                onChange={(event) => {
                  void ingestFiles(filesFromList(event.target.files));
                  event.target.value = "";
                }}
              />
              <button
                type="button"
                className="flex size-8 items-center justify-center rounded-full text-muted hover:bg-hover hover:text-ink"
                title="添加图片，也可 Ctrl+V 粘贴截图"
                disabled={streaming}
                onClick={() => fileRef.current?.click()}
              >
                <Paperclip className="size-4" />
              </button>
              <PermissionModeSelect />
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <ModelSelect />
              {streaming ? (
                <button
                  type="button"
                  className="flex size-8 items-center justify-center rounded-full bg-accent text-white"
                  onClick={() => void abort()}
                >
                  <Square className="size-3 fill-current" />
                </button>
              ) : (
                <button
                  type="button"
                  className="flex size-8 items-center justify-center rounded-full bg-accent text-white disabled:opacity-30"
                  disabled={!canSend}
                  onClick={() => void send()}
                >
                  <ArrowUp className="size-4" />
                </button>
              )}
            </div>
          </div>
        </div>
        {usage ? (
          <div className="mt-1.5 px-2 font-mono text-[11px] text-faint">
            {usage.durationMs ? `${formatDuration(usage.durationMs)} · ` : ""}
            {formatTokens(usage.inputTokens + usage.outputTokens)} tok
            {usage.contextRatio != null
              ? ` · 上下文 ${Math.round(usage.contextRatio * 100)}%`
              : ""}
          </div>
        ) : null}
      </div>
      <ImageLightbox src={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
