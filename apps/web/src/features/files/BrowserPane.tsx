import { ArrowLeft, ArrowRight, ExternalLink, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { httpUrl } from "@/lib/url";
import { usePanelStore } from "@/features/files/panel-store";

function hostLabel(url: string): string {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url || "浏览器";
  }
}

/**
 * 明确拒绝被嵌入的站点。
 *
 * 渲染进程读不到跨域响应头，没法通用探测 `X-Frame-Options`，所以这里只能列已知的。
 * 漏网的站点仍然会显示浏览器的嵌入失败页，用户用工具栏的「系统浏览器打开」兜底。
 */
const NO_EMBED_HOSTS = new Set(["github.com", "www.github.com", "gist.github.com"]);

function forbidsEmbed(url: string): boolean {
  try {
    return NO_EMBED_HOSTS.has(new URL(url).hostname.toLowerCase());
  } catch {
    return false;
  }
}

/**
 * `<webview>` 是 Electron 的内置标签，不是普通 DOM 元素，也不参与 React 的协调，
 * 因此按需要用到的成员局部声明，并由 DOM 直接挂载。
 */
type WebviewElement = HTMLDivElement & {
  /** 导航要用它：webview 挂载后给 src 赋值不会开始导航，页面会停在 about:blank。 */
  loadURL(url: string): Promise<void>;
  reload(): void;
  canGoBack(): boolean;
  canGoForward(): boolean;
  goBack(): void;
  goForward(): void;
  getURL(): string;
  getTitle(): string;
};

/** webview 往宿主派发的导航事件，React 的事件类型里没有这些。 */
type NavigateEvent = Event & { url?: string };

/** did-fail-load 带错误码；-3 是 ERR_ABORTED，重定向与用户取消都会带上它，不算失败。 */
type FailLoadEvent = Event & { errorCode?: number; isMainFrame?: boolean };

/** page-title-updated 自带新标题，SPA 改标题也会触发。 */
type TitleEvent = Event & { title?: string };

const ERR_ABORTED = -3;

/** guest 进程起不来时不会发 dom-ready。这个上限之后改成失败态，别留一块空白。 */
const GUEST_READY_TIMEOUT_MS = 15_000;

function isDesktop(): boolean {
  return typeof window !== "undefined" && window.nonoDesktop?.isDesktop === true;
}

export function BrowserPane({ tabId, url }: { tabId: string; url: string }) {
  const patchBrowser = usePanelStore((state) => state.patchBrowser);
  const desktop = isDesktop();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const tagRef = useRef<WebviewElement | null>(null);

  const [draft, setDraft] = useState(url);
  const [frame, setFrame] = useState(0);
  const [rejected, setRejected] = useState<string | null>(null);
  const [guest, setGuest] = useState<"loading" | "ready" | "failed">("loading");
  /**
   * Electron 的硬前置条件：webview 入 DOM 并且发出 dom-ready 之后，
   * getURL / loadURL / canGoBack 这些方法才可用，否则抛
   * "The WebView must be attached to the DOM and the dom-ready event emitted"。
   * 在渲染期直接调它们会让整棵 React 树崩掉，所以每个调用点都要先问这个标志。
   */
  const [attached, setAttached] = useState(false);
  const attachedRef = useRef(false);
  /** dom-ready 之前到来的地址先存着，等 guest 就绪再发过去。 */
  const pendingUrlRef = useRef("");

  // 桌面模式下 webview 是真正的浏览器，历史由它自己管，地址栏跟着它的导航事件走。
  // Web 模式下跨域 iframe 上报不了真实地址，只能由应用维护自己的历史栈。
  const [webUrl, setWebUrl] = useState(url);
  const [stack, setStack] = useState<string[]>(() => (url ? [url] : []));
  const [index, setIndex] = useState(0);
  const current = desktop ? webUrl : (stack[index] ?? "");

  // 建立 webview 并接管它的导航。partition 让访客的 cookie 与应用自身会话分开，
  // 浏览登录站点不会把凭据写进主渲染进程的会话里。
  useEffect(() => {
    if (!desktop) return;
    const host = hostRef.current;
    if (!host) return;

    const tag = document.createElement("webview") as WebviewElement;
    // webview 是替换元素，不给尺寸就停在 300×150 的默认盒子里，看起来是块空白。
    tag.style.cssText = "position:absolute;inset:0;width:100%;height:100%;border:0;background:#fff;display:flex";
    tag.setAttribute("partition", "persist:nono-browser");
    tag.setAttribute("allowpopups", "");
    // 访客页拿不到 Node，也不共享主渲染进程的上下文。
    tag.setAttribute("webpreferences", "contextIsolation=yes, nodeIntegration=no, sandbox=yes");
    // 先给一个空白起点让 guest 附着并发 dom-ready；实测不带 src 的 webview 迟迟不发
    // dom-ready，后续导航与历史接口就都用不了。真正的地址由下面的 loadURL 发。
    tag.setAttribute("src", "about:blank");

    const sync = (event: NavigateEvent): void => {
      if (!attachedRef.current) return;
      const next = event.url || tag.getURL();
      if (!httpUrl(next)) return;
      setWebUrl(next);
      patchBrowser(tabId, { url: next, title: tag.getTitle() || hostLabel(next) });
    };
    const onDomReady = (): void => {
      const first = !attachedRef.current;
      attachedRef.current = true;
      setAttached(true);
      // about:blank 是 guest 的初始页，只有停在真实页面上才算加载好了。
      if (tag.getURL() !== "about:blank") setGuest("ready");
      if (!first) return;
      // guest 就绪，把挂载期间攒下的地址发出去。必须用 loadURL：webview 挂载后
      // 给 src 赋值不会开始导航，页面会一直停在 about:blank。
      const pending = pendingUrlRef.current;
      if (pending && tag.getURL() !== pending) void tag.loadURL(pending);
    };
    const onFail = (event: FailLoadEvent): void => {
      if (event.isMainFrame === false || event.errorCode === ERR_ABORTED) return;
      setGuest("failed");
    };
    // 页面标题走 page-title-updated：SPA 改标题时不会重新导航，只靠 sync() 里的
    // getTitle() 会一直停在首屏那个标题。事件自带 title，不用碰 dom-ready 之前的接口。
    const onTitle = (event: TitleEvent): void => {
      const title = (event.title ?? "").trim();
      if (title) patchBrowser(tabId, { title });
    };
    tag.addEventListener("did-navigate", sync);
    tag.addEventListener("did-navigate-in-page", sync);
    tag.addEventListener("dom-ready", onDomReady);
    tag.addEventListener("did-fail-load", onFail);
    tag.addEventListener("page-title-updated", onTitle);

    host.append(tag);
    tagRef.current = tag;
    setGuest("loading");
    // guest 进程起不来时不会发 dom-ready，到点就转失败态，而不是留一块空白。
    const deadline = setTimeout(() => {
      setGuest((state) => (state === "ready" ? state : "failed"));
    }, GUEST_READY_TIMEOUT_MS);

    return () => {
      clearTimeout(deadline);
      tag.removeEventListener("did-navigate", sync);
      tag.removeEventListener("did-navigate-in-page", sync);
      tag.removeEventListener("dom-ready", onDomReady);
      tag.removeEventListener("did-fail-load", onFail);
      tag.removeEventListener("page-title-updated", onTitle);
      tag.remove();
      tagRef.current = null;
      attachedRef.current = false;
    };
  }, [desktop, tabId, patchBrowser]);

  // 地址栏变化推给 webview。必须先入 DOM 再导航，否则不会加载。
  // dom-ready 之前只记账，等 onDomReady 再发；已经停在该地址上就不重复导航，
  // sync() 会把 webview 自己的导航结果写回地址栏，无条件导航会把页内跳转变成整页重载。
  useEffect(() => {
    const tag = tagRef.current;
    if (!desktop || !tag || !current) return;
    if (!attachedRef.current) {
      pendingUrlRef.current = current;
      return;
    }
    if (tag.getURL() === current) return;
    void tag.loadURL(current);
  }, [desktop, current]);

  function visit(next: string) {
    const safe = httpUrl(next);
    if (!safe) {
      setRejected("只能打开 http 或 https 地址");
      return;
    }
    setRejected(null);
    setDraft(safe);
    if (desktop) {
      setWebUrl(safe);
    } else {
      const history = [...stack.slice(0, stack.length === 0 ? 0 : index + 1), safe];
      setStack(history);
      setIndex(history.length - 1);
    }
    patchBrowser(tabId, { url: safe, title: hostLabel(safe) });
  }

  function go(delta: number) {
    if (desktop) {
      const tag = tagRef.current;
      if (!tag || !attachedRef.current) return;
      if (delta < 0 && tag.canGoBack()) tag.goBack();
      if (delta > 0 && tag.canGoForward()) tag.goForward();
      return;
    }
    const next = index + delta;
    if (next < 0 || next >= stack.length) return;
    setIndex(next);
    const target = stack[next] ?? "";
    setDraft(target);
    patchBrowser(tabId, { url: target, title: hostLabel(target) });
  }

  function refresh() {
    if (desktop) {
      const tag = tagRef.current;
      if (tag && attachedRef.current) tag.reload();
      return;
    }
    setFrame((value) => value + 1);
  }

  async function openExternally() {
    if (!current) return;
    const bridge = typeof window !== "undefined" ? window.nonoDesktop?.openExternal : undefined;
    if (bridge) {
      const result = await bridge(current);
      if (!result.ok && result.error) setRejected(result.error);
      return;
    }
    window.open(current, "_blank", "noopener,noreferrer");
  }

  // canGoBack / canGoForward 在 dom-ready 之前会抛异常，而这里跑在渲染期：
  // 不加 attached 判定，创建标签页的那一次渲染就会把整棵树打崩（白屏）。
  const canBack = desktop ? attached && Boolean(tagRef.current?.canGoBack()) : index > 0;
  const canForward = desktop ? attached && Boolean(tagRef.current?.canGoForward()) : index < stack.length - 1;
  const blocked = !desktop && current !== "" && forbidsEmbed(current);

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
          disabled={!canBack}
          onClick={() => go(-1)}
        >
          <ArrowLeft className="size-3.5" />
        </button>
        <button
          type="button"
          className="flex size-7 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink disabled:opacity-40"
          title="前进"
          aria-label="前进"
          disabled={!canForward}
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
          onClick={refresh}
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
      {blocked ? (
        <div className="flex flex-1 flex-col items-start gap-2 px-4 py-6 text-[13px] leading-6 text-muted">
          <p>
            <span className="text-ink">{hostLabel(current)}</span> 用{" "}
            <span className="text-ink">X-Frame-Options: deny</span> 拒绝了被嵌入，iframe 里打不开。
          </p>
          <p>浏览器版受这条响应头约束，绕不开；桌面版走独立浏览上下文，不受影响。</p>
          <OpenExternallyButton onClick={openExternally} />
        </div>
      ) : (
        <div className="relative min-h-0 flex-1 bg-bg">
          {/* webview 的宿主必须一直挂着：只在有网址时渲染的话，新建标签页那一次
              effect 拿不到 ref 会直接返回，之后补上网址也不会再创建 webview。 */}
          {desktop ? <div ref={hostRef} className="absolute inset-0 bg-white" /> : null}
          {desktop && current && guest !== "ready" ? (
            <div className="absolute inset-0 flex flex-col items-start gap-2 bg-bg px-4 py-6 text-[13px] leading-6 text-muted">
              {guest === "failed" ? (
                <>
                  <p>内置浏览器没能打开这个页面，guest 进程可能没有起来。</p>
                  <OpenExternallyButton onClick={openExternally} />
                </>
              ) : (
                <p>正在打开 {hostLabel(current)}…</p>
              )}
            </div>
          ) : null}
          {!desktop && current ? (
            <iframe
              key={`${current}:${frame}`}
              className="absolute inset-0 border-0 bg-white"
              src={current}
              title={hostLabel(current)}
              sandbox="allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads allow-same-origin"
              referrerPolicy="no-referrer"
            />
          ) : null}
          {!current ? (
            <p className="absolute inset-0 px-4 py-6 text-[13px] leading-6 text-muted">
              输入网址后回车，页面会在这个沙箱里打开。可以多开几个浏览器标签。
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}

function OpenExternallyButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line px-2 text-[12px] text-ink hover:bg-hover"
      onClick={onClick}
    >
      <ExternalLink className="size-3.5" />
      用系统浏览器打开
    </button>
  );
}