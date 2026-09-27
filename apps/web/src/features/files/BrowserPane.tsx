import { ArrowLeft, ArrowRight, RefreshCw } from "lucide-react";
import { useState } from "react";

import { usePanelStore } from "@/features/files/panel-store";

/** 只允许 http(s)。没有协议时补上 https。 */
export function httpUrl(raw: string): string | null {
  const text = raw.trim();
  if (!text) return null;
  const withScheme = /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`;
  try {
    const url = new URL(withScheme);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.toString();
  } catch {
    return null;
  }
}

function hostLabel(url: string): string {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url || "浏览器";
  }
}

export function BrowserPane({ tabId, url }: { tabId: string; url: string }) {
  const patchBrowser = usePanelStore((state) => state.patchBrowser);
  const [draft, setDraft] = useState(url);
  const [stack, setStack] = useState<string[]>(() => (url ? [url] : []));
  const [index, setIndex] = useState(0);
  const [frame, setFrame] = useState(0);
  const [rejected, setRejected] = useState<string | null>(null);
  const current = stack[index] ?? "";

  function visit(next: string) {
    const safe = httpUrl(next);
    if (!safe) {
      setRejected("只能打开 http 或 https 地址");
      return;
    }
    setRejected(null);
    setDraft(safe);
    const history = [...stack.slice(0, stack.length === 0 ? 0 : index + 1), safe];
    setStack(history);
    setIndex(history.length - 1);
    patchBrowser(tabId, { url: safe, title: hostLabel(safe) });
  }

  function go(delta: number) {
    const next = index + delta;
    if (next < 0 || next >= stack.length) return;
    setIndex(next);
    const target = stack[next] ?? "";
    setDraft(target);
    patchBrowser(tabId, { url: target, title: hostLabel(target) });
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <form
        className="flex h-10 shrink-0 items-center gap-1 border-b border-line px-2"
        onSubmit={(event) => {
          event.preventDefault();
          visit(draft);
        }}
      >
        <button
          type="button"
          className="flex size-7 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink disabled:opacity-40"
          title="后退"
          aria-label="后退"
          disabled={index <= 0}
          onClick={() => go(-1)}
        >
          <ArrowLeft className="size-3.5" />
        </button>
        <button
          type="button"
          className="flex size-7 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink disabled:opacity-40"
          title="前进"
          aria-label="前进"
          disabled={index >= stack.length - 1}
          onClick={() => go(1)}
        >
          <ArrowRight className="size-3.5" />
        </button>
        <button
          type="button"
          className="flex size-7 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink disabled:opacity-40"
          title="刷新"
          aria-label="刷新"
          disabled={!current}
          onClick={() => setFrame((value) => value + 1)}
        >
          <RefreshCw className="size-3.5" />
        </button>
        <input
          value={draft}
          placeholder="输入网址"
          className="h-7 min-w-0 flex-1 rounded-md border border-line bg-bg px-2 text-[12px] outline-none focus:border-line-strong"
          spellCheck={false}
          onChange={(event) => setDraft(event.target.value)}
        />
      </form>
      {rejected ? <p className="border-b border-line px-3 py-1.5 text-[12px] text-danger">{rejected}</p> : null}
      {current ? (
        <iframe
          key={`${current}:${frame}`}
          className="min-h-0 flex-1 border-0 bg-white"
          src={current}
          title={hostLabel(current)}
          sandbox="allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads allow-same-origin"
          referrerPolicy="no-referrer"
        />
      ) : (
        <p className="px-4 py-6 text-[13px] leading-6 text-muted">输入网址后回车，页面会在这个沙箱里打开。可以多开几个浏览器标签。</p>
      )}
    </div>
  );
}
