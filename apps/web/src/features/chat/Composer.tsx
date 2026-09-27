import { ArrowUp, Paperclip, Square, X } from "lucide-react";
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";

import { ImageLightbox } from "@/components/ImageLightbox";
import { Textarea } from "@/components/ui/textarea";
import { CommandPalette } from "@/features/chat/CommandPalette";
import { ComposerMenu } from "@/features/chat/ComposerMenu";
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
  // 逐字段订阅：无参 useChatStore() 会订阅整个 store，流式输出时每次提交都要重渲染整个输入区。
  const input = useChatStore((state) => state.input);
  const setInput = useChatStore((state) => state.setInput);
  const send = useChatStore((state) => state.send);
  const runCommand = useChatStore((state) => state.runCommand);
  const abort = useChatStore((state) => state.abort);
  const streaming = useChatStore((state) => state.streaming);
  const commands = useChatStore((state) => state.commands);
  const error = useChatStore((state) => state.error);
  const usage = useChatStore((state) => state.usage);
  const attachments = useChatStore((state) => state.attachments);
  const addAttachments = useChatStore((state) => state.addAttachments);
  const removeAttachment = useChatStore((state) => state.removeAttachment);
  const setError = useChatStore((state) => state.setError);
  const [menuOpen, setMenuOpen] = useState<"plus" | "connectors" | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const slashQuery = input.startsWith("/") ? input.slice(1).split(/\s/)[0] ?? "" : "";
  const slashOpen = input.startsWith("/");
  const canSend = Boolean(input.trim() || attachments.length);

  useEffect(() => {
    if (!menuOpen && !slashOpen) return;
    function onPointer(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(null);
    }
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      setMenuOpen(null);
      if (input.startsWith("/")) setInput("");
    }
    document.addEventListener("pointerdown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpen, slashOpen, input, setInput]);

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
    function onInsert(event: Event) {
      const text = (event as CustomEvent<string>).detail;
      if (!text) return;
      const area = areaRef.current;
      const current = useChatStore.getState().input;
      const start = area?.selectionStart ?? current.length;
      const end = area?.selectionEnd ?? current.length;
      const prefix = current.slice(0, start);
      const gap = prefix.length > 0 && !/[\s\n]$/.test(prefix) ? " " : "";
      const inserted = gap + text;
      setInput(prefix + inserted + current.slice(end));
      const caret = start + inserted.length;
      window.requestAnimationFrame(() => {
        area?.focus();
        area?.setSelectionRange(caret, caret);
      });
    }
    window.addEventListener("nono-insert-input", onInsert);
    return () => window.removeEventListener("nono-insert-input", onInsert);
  }, [setInput]);

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

  function onKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
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
            ref={areaRef}
            rows={2}
            value={input}
            placeholder="描述你想做的事，可粘贴截图或添加图片"
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <div className="mt-2 flex items-center justify-between gap-2">
            <div ref={menuRef} className="relative flex min-w-0 items-center gap-1">
              <CommandPalette
                open={slashOpen}
                query={slashQuery}
                commands={commands.length ? commands : FALLBACK_COMMANDS}
                onPick={(command) => {
                  setMenuOpen(null);
                  void runCommand(`/${command.name}`);
                }}
              />
              <ComposerMenu
                open={slashOpen ? null : menuOpen}
                onOpenChange={setMenuOpen}
                disabled={streaming}
                onAddImages={() => fileRef.current?.click()}
              />
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
