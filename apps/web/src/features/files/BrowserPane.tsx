import {
  ArrowLeft,
  ArrowRight,
  Bug,
  Camera,
  Crop,
  ExternalLink,
  MousePointerClick,
  RefreshCw,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { httpUrl } from "@/lib/url";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";
import { usePanelStore } from "@/features/files/panel-store";
import {
  captureViewport,
  clampRect,
  cropCapture,
  describeElement,
  pendingImageFromPng,
  type CaptureRect,
  type PickedElement,
  type WebviewElement,
} from "@/features/files/browser-tools";
import { ElementPicker } from "@/features/files/ElementPicker";
import { RegionSelect } from "@/features/files/RegionSelect";

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

/** webview 往宿主派发的导航事件，React 的事件类型里没有这些。 */
type NavigateEvent = Event & { url?: string };

/** did-fail-load 带错误码；-3 是 ERR_ABORTED，重定向与用户取消都会带上它，不算失败。 */
type FailLoadEvent = Event & { errorCode?: number; isMainFrame?: boolean };

/** page-title-updated 自带新标题，SPA 改标题也会触发。 */
type TitleEvent = Event & { title?: string };

const ERR_ABORTED = -3;

/** guest 进程起不来时不会发 dom-ready。这个上限之后改成失败态，别留一块空白。 */
const GUEST_READY_TIMEOUT_MS = 15_000;

/** 停靠面板的宽度下限 / 页面区的保底宽度。再窄两边都没法看了。 */
const MIN_PANEL = 280;
const MIN_PAGE = 240;

function isDesktop(): boolean {
  return typeof window !== "undefined" && window.nonoDesktop?.isDesktop === true;
}

export function BrowserPane({ tabId, url }: { tabId: string; url: string }) {
  const patchBrowser = usePanelStore((state) => state.patchBrowser);
  const addAttachments = useChatStore((state) => state.addAttachments);
  const addQuote = useChatStore((state) => state.addQuote);
  const desktop = isDesktop();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const tagRef = useRef<WebviewElement | null>(null);

  const [draft, setDraft] = useState(url);
  const [frame, setFrame] = useState(0);
  const [rejected, setRejected] = useState<string | null>(null);
  const [guest, setGuest] = useState<"loading" | "ready" | "failed">("loading");
  /** 框选 / 拾取两种模式互斥：两个覆盖层都盖在 webview 上，同时开只会抢事件。 */
  const [mode, setMode] = useState<"idle" | "region" | "pick">("idle");
  /** 截图链路（IPC + 裁剪 + 编码）要跑一会儿，按钮期间得压住，避免连点叠出多张图。 */
  const [busy, setBusy] = useState(false);
  const [devToolsOpen, setDevToolsOpen] = useState(false);
  /** 停靠失败的原因。直接画在面板里 —— 让人去另一个终端翻日志不算做好诊断。 */
  const [devToolsError, setDevToolsError] = useState<string | null>(null);
  /** 停靠成功与否的说明，贴在面板底部。让这块地方永远不可能是纯白。 */
  const [devToolsStatus, setDevToolsStatus] = useState<string | null>(null);
  /** 停靠面板的宽度，用户拖分隔条改。 */
  const [devToolsWidth, setDevToolsWidth] = useState(560);
  /** 拖拽中。页面容器要靠它把 pointer-events 关掉，别让 <webview> 抢走鼠标。 */
  const [splitDragging, setSplitDragging] = useState(false);
  /** DevTools 前端专用的 webview（页面 webview 的兄弟节点，不是它的孩子）。 */
  const devToolsHostRef = useRef<HTMLDivElement | null>(null);
  /** 页面与停靠面板的容器，用来把拖拽宽度夹在合理范围内。 */
  const splitRef = useRef<HTMLDivElement | null>(null);
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

  /**
   * 只有 webview 才有的能力。iframe 版跨域页面在 canvas 上是被污染的，
   * toDataURL 会直接抛安全错误 —— 与其点了报错，不如按钮就按下去。
   *
   * 用 attached 这个 state 而不是 attachedRef：ref 在渲染期读不会触发重渲染，
   * 按钮的亮灭得跟着 dom-ready 真的走一遍。attached 和 guest 在 onDomReady 里
   * 是一起翻的，所以一次重渲染就够。
   */
  const captureReady = desktop && Boolean(current) && attached && guest === "ready";

  function toggleDevTools() {
    setDevToolsOpen((open) => {
      setDevToolsError(null);
      setDevToolsStatus(open ? null : "正在停靠…");
      return !open;
    });
  }

  /**
   * 停靠的生命周期与位置同步。
   *
   * 这里只剩一个空 div —— DevTools 前端由主进程的 WebContentsView 承载，它不在 DOM 里，
   * 所以这块 div 只负责「占住位置」：把 getBoundingClientRect() 推给主进程，
   * 主进程拿它去 setBounds()。两边坐标能直接对得上，因为窗口是 titleBarStyle:'hidden'，
   * web contents 铺满整个内容区，DOM 原点就是 setBounds 的原点。
   *
   * 为什么不用 webview 当宿主：试过，前端能加载但拿不到调试后端连接（加载出来的地址
   * 没有 ws= 参数，退化成去连公网 remoteBase），表现是"面板完整、一条数据没有"。
   */
  useEffect(() => {
    if (!devToolsOpen) return;
    const host = devToolsHostRef.current;
    const page = tagRef.current;
    if (!host || !page || !attachedRef.current) return;

    const bridge = typeof window === "undefined" ? undefined : window.nonoDesktop;
    // 先把方法取出来再用：bridge 是可选对象，收窄结果带不进下面的闭包。
    const attachDevtools = bridge?.attachDevtools;
    const moveDevtools = bridge?.moveDevtools;
    const detachDevtools = bridge?.detachDevtools;
    if (!attachDevtools) {
      setDevToolsOpen(false);
      return;
    }

    let pageId: number;
    try {
      pageId = page.getWebContentsId();
    } catch {
      setDevToolsOpen(false);
      return;
    }

    const report = () => {
      const box = host.getBoundingClientRect();
      // 量到 0 宽高说明这块还没布局好，推过去会把视图缩成一条缝，不如先不动。
      if (box.width < 1 || box.height < 1) return;
      void moveDevtools?.({ x: box.left, y: box.top, width: box.width, height: box.height });
    };
    // 要观察**两个**目标，少一个都会漏：
    //   占位块 —— 宽度是用户拖分隔条定的，拖完必须立刻跟上；
    //   外层容器 —— 左右任何一侧的面板被拖宽、折叠，窗口缩放，改的都是这一块的
    //     几何形状。只观察占位块是不够的：它宽度写死，外部挤压只改**位置**，
    //     而 ResizeObserver 只认尺寸，于是 DevTools 视图就停在老地方不动了。
    const observer = new ResizeObserver(report);
    observer.observe(host);
    const split = splitRef.current;
    if (split) observer.observe(split);
    window.addEventListener("resize", report);
    // 纵向滚动不会改变上面任何一块的尺寸，但会改占位块的位置。
    window.addEventListener("scroll", report, true);

    void attachDevtools(pageId, { x: 0, y: 0, width: 1, height: 1 }).then((result) => {
      // 主进程会等前端真的加载完才回执；失败时**保留面板**并把原因画出来。
      if (result.ok) {
        setDevToolsStatus(`已停靠 · ${result.url ?? "devtools://…"}`);
        report();
      } else {
        setDevToolsError(result.error ?? "DevTools 打不开");
        setDevToolsStatus("停靠失败");
        toast(result.error ?? "DevTools 打不开");
      }
    });

    return () => {
      observer.disconnect();
      window.removeEventListener("resize", report);
      window.removeEventListener("scroll", report, true);
      void detachDevtools?.();
    };  }, [devToolsOpen]);

  /**
   * 拖分隔条调停靠面板宽度。页面区至少留 240px，不然网页会被压到没法看。
   *
   * 麻烦在于拖拽途中鼠标会经过**两片会抢鼠标的地方**：
   *   左边页面的 <webview> 是独立浏览上下文，鼠标进去 DOM 就收不到 pointermove；
   *   右边 DevTools 是主进程的原生视图（WebContentsView），光标一进去，整个渲染
   *   进程连 pointerup 都收不到 —— 拖拽会卡在中途，鼠标也放不回来。
   * 两边都得让开：
   *   左边 —— 给页面容器加 pointer-events:none，一条 CSS 搞定；
   *   右边 —— 拖拽期间让主进程把那个视图 setVisible(false)。注意不能指望
   *     setIgnoreMouseEvents：那个方法只在 BrowserWindow/BaseWindow 上，View 基类
   *     没有，WebContentsView 继承的正是 View。
   * 松手时把最终 bounds 一起交给主进程，先摆位置再显示，避免按旧宽度闪一帧。
   */
  function startSplitDrag(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const split = splitRef.current;
    if (!split) return;
    const host = devToolsHostRef.current;
    const startX = event.clientX;
    const startWidth = devToolsWidth;
    const limit = () => split.getBoundingClientRect().width;
    const setDragging =
      typeof window === "undefined" ? undefined : window.nonoDesktop?.dragDevtools;

    setSplitDragging(true);
    // 光标样式是逐元素解析的，鼠标一离开分隔条就会变回箭头。全局锁一次，
    // 拖到哪儿都是 col-resize，拖完再撤。
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    void setDragging?.(true);

    const move = (moveEvent: PointerEvent) => {
      // 面板在右边，往左拖才是变宽。
      const next = startWidth + (startX - moveEvent.clientX);
      setDevToolsWidth(Math.max(MIN_PANEL, Math.min(next, limit() - MIN_PAGE)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setSplitDragging(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      // React 的 setState 是异步的，这一帧的 host 尺寸还是拖之前的。直接量当前
      // 的几何位置报过去 —— 占位块此刻已经跟着新宽度重排过了，量得准。
      const box = host?.getBoundingClientRect();
      void setDragging?.(
        false,
        box && box.width >= 1 && box.height >= 1
          ? { x: box.left, y: box.top, width: box.width, height: box.height }
          : undefined,
      );
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  /** 截图三件套：主进程截一张 → 渲染进程裁出选区 → 编码成对话框附件。 */
  async function grab(
    label: string,
    rect: CaptureRect | null,
    viewport: { width: number; height: number },
  ) {
    const tag = tagRef.current;
    if (!tag) return;
    setBusy(true);
    try {
      const { data, error } = await captureViewport(tag);
      if (!data) {
        toast(error ?? "截图失败");
        return;
      }
      let base64 = data;
      if (rect) {
        const box = clampRect(rect, viewport.width, viewport.height);
        if (box.width < 8 || box.height < 8) {
          toast("选区太小了");
          return;
        }
        base64 = await cropCapture(data, viewport.width, box);
      }
      addAttachments([await pendingImageFromPng(label, base64)]);
      toast("已加进对话框");
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "截图失败");
    } finally {
      setBusy(false);
    }
  }

  /** webview 元素和访客页视口是 1:1 的，元素的 CSS 尺寸就是视口 CSS 尺寸。 */
  function viewportSize() {
    const box = tagRef.current?.getBoundingClientRect();
    return { width: box?.width ?? 0, height: box?.height ?? 0 };
  }

  function snapshotViewport() {
    if (!captureReady) return;
    void grab(`页面截图 ${hostLabel(current)}`, null, viewportSize());
  }

  function submitRegion(rect: CaptureRect) {
    setMode("idle");
    const tag = tagRef.current;
    if (!tag) return;
    const viewport = viewportSize();
    void grab(`框选 ${Math.round(rect.width)}×${Math.round(rect.height)}`, rect, viewport);
  }

  /**
   * 元素拾取：引用（地址 + 选择器 + HTML）是主体，裁剪出来的图是补充。
   * 图截不到不该让整次拾取作废 —— 用户点这个按钮想要的是那段 DOM 描述。
   */
  async function submitPick(element: PickedElement) {
    setMode("idle");
    const tag = tagRef.current;
    addQuote(describeElement(element), "browser");
    if (!tag) {
      toast("已把选中元素加进对话框");
      return;
    }
    const viewport = viewportSize();
    try {
      const { data } = await captureViewport(tag);
      if (data) {
        const box = clampRect(element, viewport.width, viewport.height);
        if (box.width >= 8 && box.height >= 8) {
          addAttachments([await pendingImageFromPng(`元素 ${element.tag}`, await cropCapture(data, viewport.width, box))]);
        }
      }
    } catch {
      // 裁剪失败就算了，引用已经在对话框里。
    }
    toast("已把选中元素加进对话框");
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
        {/* 工具区。用一条竖线跟地址栏隔开：这几个是「对当前页面做什么」，
            跟左边那三个「在历史里挪位置」不是一类动作，混在一排会点错。

            底下这四个（截图 / 框选 / 拾取 / DevTools）都依赖 <webview>：像素在主进程
            手里、DOM 要 executeJavaScript、DevTools 要 WebContentsView。纯 web 版走的是
            跨域 <iframe>，一个都给不了，所以**整个不渲染**，而不是画成灰的再挂个
            "不可用"的 tooltip —— 摆在那儿只会让人反复去点一个永远不亮的东西。
            只有「用系统浏览器打开」双端可用（没有桥时退回 window.open）。 */}
        <span className="h-4 w-px shrink-0 bg-line" />
        <ToolbarButton
          label="用系统浏览器打开"
          onClick={() => void openExternally()}
          disabled={!current}
        >
          <ExternalLink className="size-3.5" />
        </ToolbarButton>
        {desktop ? (
          <>
            <ToolbarButton
              label="把当前页面截成图片加进对话框"
              onClick={snapshotViewport}
              disabled={!captureReady || busy}
            >
              <Camera className="size-3.5" />
            </ToolbarButton>
            <ToolbarButton
              label={mode === "region" ? "正在框选，Esc 取消" : "框选一片区域，截成图片加进对话框"}
              onClick={() => setMode(mode === "region" ? "idle" : "region")}
              disabled={!captureReady || busy}
              active={mode === "region"}
            >
              <Crop className="size-3.5" />
            </ToolbarButton>
            <ToolbarButton
              label={mode === "pick" ? "正在拾取，Esc 取消" : "拾取页面元素，把它的地址和 HTML 加进对话框"}
              onClick={() => setMode(mode === "pick" ? "idle" : "pick")}
              disabled={!captureReady || busy}
              active={mode === "pick"}
            >
              <MousePointerClick className="size-3.5" />
            </ToolbarButton>
            <ToolbarButton
              label={devToolsOpen ? "关闭开发者工具" : "打开开发者工具"}
              onClick={toggleDevTools}
              disabled={!captureReady}
              active={devToolsOpen}
            >
              <Bug className="size-3.5" />
            </ToolbarButton>
          </>
        ) : null}
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
        // 停靠 DevTools 时这里从一块变成左右两栏：左栏是页面，右栏是 DevTools 前端
        // 自己的 webview（页面 webview 的兄弟，不是孩子 —— 塞进去会被页面的布局
        // 管走，而 DevTools 得跟页面完全独立）。
        <div ref={splitRef} className="flex min-h-0 flex-1 bg-bg">
          {/* 拖拽期间整块让开指针事件：页面里的 <webview> 是独立浏览上下文，鼠标进去
              DOM 就收不到事件，宽度会卡住不动。关掉 pointer-events 就把控制权交回
              我们的 window 级监听。 */}
          <div className={cn("relative min-h-0 min-w-0 flex-1", splitDragging && "pointer-events-none")}>
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
            {/* 两种覆盖层都只活在页面上，且互斥：都盖在同一个位置，
                同时开的话谁也拿不到指针事件。切换时先退掉旧模式。 */}
            {mode === "region" && tagRef.current ? (
              <RegionSelect onCancel={() => setMode("idle")} onSubmit={submitRegion} />
            ) : null}
            {mode === "pick" && tagRef.current ? (
              <ElementPicker tag={tagRef.current} onCancel={() => setMode("idle")} onSubmit={(el) => void submitPick(el)} />
            ) : null}
            {!current ? (
              <p className="absolute inset-0 px-4 py-6 text-[13px] leading-6 text-muted">
                输入网址后回车，页面会在这个沙箱里打开。可以多开几个浏览器标签。
              </p>
            ) : null}
          </div>
          {devToolsOpen && desktop ? (
            <>
              {/* 分隔条。命中区 10px、实线 2px：1px 太难点，8px 以上又有"这块很宽"的
                  错觉。拖的时候整条变主色，光标换成 col-resize，让手上有"抓到了"的反馈。 */}
              <div
                className={cn(
                  "w-2.5 shrink-0 cursor-col-resize transition-colors",
                  splitDragging ? "bg-accent" : "bg-line hover:bg-line-strong",
                )}
                role="separator"
                aria-orientation="vertical"
                aria-label="调整开发者工具宽度"
                title="拖动调整开发者工具宽度"
                onPointerDown={startSplitDrag}
                onDoubleClick={() => setDevToolsWidth(560)}
              />
              {/* 占位块。DevTools 前端不在 DOM 里，是主进程的一个原生视图盖在这个
                  位置上，所以这里保持透明、什么都不画，只负责量出位置报回去。
                  底下留一层深色：视图定位的间隙里露出白底会闪一下。 */}
              <div className="relative flex min-h-0 shrink-0 flex-col" style={{ width: devToolsWidth }}>
                <div ref={devToolsHostRef} className="relative min-h-0 flex-1 bg-bg" />
                {devToolsStatus ? (
                  <div
                    className={cn(
                      "shrink-0 truncate border-t border-line px-2 py-1 font-mono text-[10px]",
                      devToolsError ? "text-danger" : "text-faint",
                    )}
                  >
                    {devToolsStatus}
                  </div>
                ) : null}
                {devToolsError ? (
                  <div className="absolute inset-0 flex flex-col gap-2 overflow-auto bg-bg px-4 py-6 text-[12px] leading-6">
                    <p className="text-[13px] font-medium text-danger">DevTools 没有加载起来</p>
                    <p className="text-muted">{devToolsError}</p>
                    <p className="text-faint">
                      前端跑在 <span className="font-mono">devtools://</span> 协议上，加载失败不会抛异常，
                      只能靠加载后的地址判断。
                    </p>
                    <p className="text-faint">
                      主进程终端里搜 <span className="font-mono">[devtools]</span> 有更详细的记录。
                    </p>
                  </div>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

function ToolbarButton({
  label,
  onClick,
  disabled,
  active,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  /** 模式类按钮按下时点亮，免得开着覆盖层却看不出来。 */
  active?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={cn(
        "flex size-7 shrink-0 items-center justify-center rounded-md transition-colors disabled:opacity-40",
        active ? "bg-active text-ink" : "text-faint hover:bg-hover hover:text-ink",
      )}
      title={label}
      aria-label={label}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
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