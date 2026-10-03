import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { Check, Copy, Reply } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { createPortal } from "react-dom";
import "@xterm/xterm/css/xterm.css";

import { fetchTerminalShells, startTerminal, stopTerminal, type TerminalShell } from "@/api/client";
import { usePanelStore } from "@/features/files/panel-store";
import { getSidebarPrefs, subscribeSidebarPrefs } from "@/features/settings/sidebar-prefs";
import { readClipboardText, writeClipboardText } from "@/lib/clipboard";
import { useResolvedTheme } from "@/lib/theme";
import { useChatStore } from "@/store/chat";

function terminalWsUrl(id: string): string {
  let base = window.location.origin;
  if (import.meta.env.DEV) {
    try {
      base = __NONO_SIDECAR_HTTP__;
    } catch {
      base = "http://127.0.0.1:8000";
    }
  }
  const url = new URL(`/app/terminal/${id}/ws`, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/**
 * 把 CSS 的颜色值翻译成 xterm 认得的写法。
 *
 * xterm 6 自己解析颜色，正则是 `/rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3}).../`，
 * 只认逗号分隔。所以 `rgb(17 24 39 / 0.16)` 这种 CSS Color 4 的空格加斜杠写法它会
 * 匹配不上，然后**静默退回内置默认** `rgba(255, 255, 255, 0.3)` —— 一层 30% 的白，
 * 叠在浅色终端底上等于什么都没画：选中了但看上去和没选一样。
 * 这层翻译放在这里，token 就还能按现代写法写，xterm 那头也不会再悄悄退化。
 */
function toXtermColor(value: string): string {
  const match = /^rgba?\(([^)]*)\)$/.exec(value.trim());
  if (!match) return value;
  const [rgb, alpha] = match[1].split("/").map((part) => part.trim());
  if (rgb.includes(",")) return value; // 已经是逗号写法，原样给
  const parts = rgb.split(/\s+/);
  return alpha ? `rgba(${parts.join(", ")}, ${alpha})` : `rgb(${parts.join(", ")})`;
}

function terminalTheme() {
  const style = getComputedStyle(document.documentElement);
  const pick = (name: string, fallback: string) => toXtermColor(style.getPropertyValue(name).trim() || fallback);
  return {
    background: pick("--color-bg", "#ffffff"),
    foreground: pick("--color-ink", "#0f1115"),
    cursor: pick("--color-ink", "#111827"),
    // 选区色也走 token：xterm 要的是实色 rgba，深浅两套的选区浓度不一样，写死一边另一边就看不见。
    selectionBackground: pick("--term-selection", "rgba(17, 24, 39, 0.16)"),
    // 失焦选区单独一份：右键菜单一开终端就失焦，这时正是在挑"复制"，选区不能跟着淡到看不见。
    selectionInactiveBackground: pick("--term-selection-inactive", "rgba(17, 24, 39, 0.09)"),
  };
}

export function TerminalPane({
  tabId,
  serverId,
  shell,
  cwd,
  root,
  active,
}: {
  tabId: string;
  serverId: string;
  shell: string;
  cwd: string;
  root: string;
  active: boolean;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  /** 直接往 PTY 写一个控制序列（清屏走 Ctrl+L），不必伪装成粘贴。 */
  const sendRef = useRef<((data: string) => void) | null>(null);
  /** 组件是否还活着。effect 外的异步流程（切 shell）需要它来避免留下孤儿进程。 */
  const aliveRef = useRef(true);
  /** 由挂载 effect 装填，供「重新创建」按钮调用。 */
  const recreateRef = useRef<(() => void) | null>(null);
  /** 菜单开合代数：异步读剪贴板回来后比对，避免对着已经关掉的菜单误粘贴。 */
  const menuTokenRef = useRef(0);
  /** 选区悬浮条的锚点：xterm 选区不是 DOM range，拿不到矩形，就落在最后一次指针位置上。 */
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  const selectingRef = useRef(false);
  const [menu, setMenu] = useState<{ x: number; y: number; hasSelection: boolean; hint: string | null } | null>(null);
  const [bar, setBar] = useState<{ text: string; x: number; y: number } | null>(null);
  const [barCopied, setBarCopied] = useState(false);
  const addQuote = useChatStore((state) => state.addQuote);
  const patchTerminal = usePanelStore((state) => state.patchTerminal);
  const [shells, setShells] = useState<TerminalShell[]>([]);
  const [status, setStatus] = useState<
    "connecting" | "open" | "closed" | "gone" | "error"
  >("connecting");
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fontSize = useSyncExternalStore(subscribeSidebarPrefs, getSidebarPrefs, getSidebarPrefs).terminalFontSize;
  const theme = useResolvedTheme();

  // xterm 在构造时就把颜色烤进自己的画布，切主题不会自己跟着变，得重新喂一次。
  useEffect(() => {
    const term = termRef.current;
    if (term) term.options.theme = terminalTheme();
  }, [theme]);

  // 字号改成立即生效：把它放进下面那个挂载 effect 的依赖里，输入「15」会先触发
  // 「1」再触发「15」，每次都销毁 xterm 并断开 WebSocket，屏幕上的历史会被清空。
  useEffect(() => {
    if (termRef.current) termRef.current.options.fontSize = fontSize;
  }, [fontSize]);

  useEffect(() => {
    void fetchTerminalShells()
      .then(setShells)
      .catch(() => setShells([]));
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const term = new Terminal({
      fontFamily: 'ui-monospace, "Cascadia Mono", Consolas, monospace',
      fontSize,
      theme: terminalTheme(),
      cursorBlink: true,
      scrollback: 5000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    termRef.current = term;
    aliveRef.current = true;
    let socket: WebSocket | null = null;
    let disposed = false;

    const sendResize = () => {
      if (host.clientWidth < 20 || host.clientHeight < 20) return;
      fit.fit();
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };

    /**
     * 用服务端的一帧完整屏幕重建视口。
     *
     * 服务端持有模拟器，所以这帧 ANSI 流是语法完整、尺寸精确的：reset 之后
     * write 它，屏幕与离开时逐格一致。这条路径同时用于首次连接、切换工作区后
     * 回来，以及慢客户端被要求补发（RESYNC）。
     */
    const applySnapshot = (frame: { cols: number; rows: number; screen: string; state: string; exitCode: number | null }) => {
      term.reset();
      if (term.cols !== frame.cols || term.rows !== frame.rows) term.resize(frame.cols, frame.rows);
      // 写完立刻重新量一次尺寸：快照是入队那一刻拍的，而 onopen 已经先发过一次
      // resize，不主动收敛的话画面会先缩回旧尺寸、等下一次 ResizeObserver 才弹回来。
      term.write(frame.screen, () => sendResize());
      if (frame.state === "exited") {
        setExitCode(frame.exitCode);
        setStatus("closed");
      } else {
        setExitCode(null);
        setStatus("open");
      }
    };

    const connect = (id: string) => {
      const next = new WebSocket(terminalWsUrl(id));
      next.binaryType = "arraybuffer";
      socket = next;
      next.onopen = () => {
        setError(null);
        // 状态由第一帧 snapshot 决定；onopen 只负责把尺寸对齐到服务端。
        sendResize();
        term.focus();
      };
      next.onmessage = (event) => {
        // 迟到的帧不能写进已经 dispose 的 xterm。
        if (disposed) return;
        if (typeof event.data === "string") {
          let payload: {
            type?: string;
            code?: number | null;
            cols?: number;
            rows?: number;
            screen?: string;
            state?: string;
            exitCode?: number | null;
          };
          try {
            payload = JSON.parse(event.data);
          } catch {
            return;
          }
          if (payload.type === "snapshot" && payload.screen && payload.cols && payload.rows) {
            // 首次连接、切换工作区回来、慢客户端补发，走的都是这一条。
            applySnapshot(payload as Parameters<typeof applySnapshot>[0]);
            return;
          }
          if (payload.type === "exit") {
            setExitCode(payload.code ?? null);
            setStatus("closed");
            return;
          }
          if (payload.type === "missing") {
            // 服务端没有这个会话了。不要自动重建：新的 shell 是另一个进程，
            // cwd、环境和历史都不同，静默换掉会让人以为自己还在原来的终端里。
            setStatus("gone");
            return;
          }
          return;
        }
        term.write(new Uint8Array(event.data as ArrayBuffer));
      };
      next.onclose = (event) => {
        if (disposed) return;
        if (event.code === 4404) {
          setStatus("gone");
          return;
        }
        setStatus((current) => (current === "closed" || current === "gone" ? current : "closed"));
      };
      next.onerror = () => {
        if (!disposed) setStatus("error");
      };
    };

    /**
     * 显式重建：只在用户点「重新创建」时才走。
     * 服务端没有的会话不能被自动复活，理由见 onmessage 里的 missing 分支。
     */
    async function recreate() {
      try {
        const box = hostRef.current?.getBoundingClientRect();
        const created = await startTerminal({
          root,
          cwd,
          shell,
          cols: box && box.width > 40 ? term.cols : 80,
          rows: box && box.height > 40 ? term.rows : 24,
        });
        if (disposed) {
          void stopTerminal(created.id);
          return;
        }
        patchTerminal(tabId, { serverId: created.id, shell: created.shell });
      } catch (cause) {
        if (!disposed) {
          setStatus("error");
          setError(cause instanceof Error ? cause.message : "无法创建终端");
        }
      }
    }
    recreateRef.current = () => void recreate();

    const disposable = term.onData((data) => {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input", data }));
      }
    });
    // 选区一变就同步悬浮条（含键盘 Shift+方向键扩选）。
    const selectionDisposable = term.onSelectionChange(() => {
      if (selectingRef.current) return;
      syncBar();
    });
    sendRef.current = (data) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data }));
    };
    connect(serverId);
    const observer = new ResizeObserver(() => sendResize());
    observer.observe(host);
    return () => {
      disposed = true;
      aliveRef.current = false;
      termRef.current = null;
      sendRef.current = null;
      disposable.dispose();
      selectionDisposable.dispose();
      observer.disconnect();
      socket?.close();
      term.dispose();
      recreateRef.current = null;
    };
    // fontSize 不在这里：它由上面的独立 effect 立即式应用，放进来会让每次按键
    // 都销毁 xterm 并断开 WebSocket。
  }, [cwd, patchTerminal, root, serverId, shell, tabId]);

  useEffect(() => {
    if (!active) return;
    termRef.current?.focus();
    // ResizeObserver 已经覆盖了标签页从隐藏变可见的尺寸变化，这里不需要额外派发。
  }, [active]);

  async function switchShell(nextShell: string) {
    if (nextShell === shell) return;
    const chosen = shells.find((item) => item.id === nextShell);
    setStatus("connecting");
    setError(null);
    await stopTerminal(serverId).catch(() => undefined);
    try {
      // 沿用当前视口的尺寸：固定 80x24 会让切完 shell 之后画面跳一下。
      const box = hostRef.current?.getBoundingClientRect();
      const term = termRef.current;
      const created = await startTerminal({
        root,
        cwd,
        shell: nextShell,
        cols: box && box.width > 40 && term ? term.cols : 80,
        rows: box && box.height > 40 && term ? term.rows : 24,
      });
      // 标签页可能已经关掉或者工作区已经切走：此时 patchTerminal 匹配不到任何
      // tab，是个静默的 no-op，刚起的 shell 就成了永不回收的孤儿。
      if (!aliveRef.current) {
        void stopTerminal(created.id);
        return;
      }
      patchTerminal(tabId, {
        serverId: created.id,
        shell: created.shell,
        title: chosen?.label || created.label,
      });
    } catch (cause) {
      setStatus("error");
      setError(cause instanceof Error ? cause.message : "无法切换 shell");
    }
  }

  function onContextMenu(event: ReactMouseEvent<HTMLDivElement>) {
    // 拦下系统菜单，我们自己出一个。xterm 挂在同一个事件上的监听只做右键选词，不冲突。
    event.preventDefault();
    menuTokenRef.current += 1;
    setMenu({
      x: event.clientX,
      y: event.clientY,
      hasSelection: Boolean(termRef.current?.getSelection()),
      hint: null,
    });
    // 右键选词也会触发选区变化，但这一刻要挑的是菜单里的复制/粘贴，悬浮条先收起来，
    // 免得两块浮层叠在一起。清掉锚点，在下一次指针移动或选区变化前都不会再冒出来。
    hideBar();
  }

  function onPointerDown(event: ReactMouseEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    selectingRef.current = true;
    // 重新框选时先收掉上一条，别让它挂在别的位置不走。
    setBar(null);
  }

  function onPointerMove(event: ReactMouseEvent<HTMLDivElement>) {
    pointerRef.current = { x: event.clientX, y: event.clientY };
  }

  async function copyBarText() {
    if (!bar) return;
    try {
      await writeClipboardText(bar.text);
      setBarCopied(true);
    } catch {
      setBarCopied(false);
    }
  }

  function quoteBarText() {
    if (!bar) return;
    addQuote(bar.text, "terminal");
    setBar(null);
  }

  const closeMenu = useCallback(() => {
    menuTokenRef.current += 1;
    setMenu(null);
    // 菜单点完要把焦点还给终端的隐藏 textarea，否则用户接着敲键盘没反应。
    termRef.current?.focus();
  }, []);

  const dismissMenu = useCallback(() => setMenu(null), []);

  /**
   * 按当前选区同步悬浮条。
   *
   * xterm 的选区不是 DOM range，拿不到 client rect，所以锚点只能用最后一次指针位置：
   * 拖选时 `selectingRef` 为真不出条（跟着鼠标闪），松手或键盘扩展选区才出。
   * 选中文本没变时不重设 state，xterm 一次拖选会连着抛十几个 selectionchange。
   */
  const syncBar = useCallback(() => {
    const text = termRef.current?.getSelection() ?? "";
    const anchor = pointerRef.current;
    if (!text.trim() || !anchor) {
      setBar(null);
      return;
    }
    setBar((current) => (current?.text === text ? current : { text, x: anchor.x, y: anchor.y }));
  }, []);

  const hideBar = useCallback(() => {
    pointerRef.current = null;
    setBar(null);
  }, []);

  /** Esc：收起悬浮条的同时把终端里的选区也清掉，否则选区会孤零零地留在屏幕上。 */
  const dismissBar = useCallback(() => {
    termRef.current?.clearSelection();
    hideBar();
  }, [hideBar]);

  // 选中的内容换了，"已复制"的提示不能还挂着。
  useEffect(() => {
    setBarCopied(false);
  }, [bar?.text]);

  /**
   * 松手判定挂在 window 上，不能只挂终端容器。
   *
   * 从终端里往外拖选（拖到聊天区或输入框才撒手）时，pointerup 根本不会经过容器，
   * `selectingRef` 会一直卡在 true，之后所有选区变化都被当成"还在拖"吞掉 —— 既不
   * 出条，键盘扩选也不出。挂到 window 才能保证这个标志一定被复位。
   *
   * 落点在终端外面就不出条：这种选区多半横跨到别的面板，条挂在上一次指针位置纯属误导。
   */
  useEffect(() => {
    function onPointerUp(event: PointerEvent) {
      if (event.button !== 0 || !selectingRef.current) return;
      selectingRef.current = false;
      const host = hostRef.current;
      if (!host) return;
      const rect = host.getBoundingClientRect();
      const inside =
        event.clientX >= rect.left &&
        event.clientX <= rect.right &&
        event.clientY >= rect.top &&
        event.clientY <= rect.bottom;
      if (!inside) {
        setBar(null);
        return;
      }
      // 同一 tick 里 xterm 才把选区写完，放到下一帧再取。
      window.requestAnimationFrame(syncBar);
    }
    function onPointerCancel() {
      selectingRef.current = false;
      setBar(null);
    }
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerCancel);
    return () => {
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerCancel);
    };
  }, [syncBar]);

  useEffect(() => {
    // 终端标签只是被 hidden 隐藏、没有卸载，两块浮层挂在 body 上，收起时必须自己撤，
    // 不然切到别的标签它们还飘在那个位置上。
    if (active) return;
    menuTokenRef.current += 1;
    setMenu(null);
    hideBar();
  }, [active, hideBar]);

  function fail(hint: string) {
    setMenu((current) => (current ? { ...current, hint } : current));
  }

  async function pasteFromClipboard() {
    // 读剪贴板是异步的（浏览器里还可能弹权限框）。期间用户可能已经关掉菜单或又
    // 在别处右键开了新的，这时候这次粘贴就不该再落到终端里。
    const token = menuTokenRef.current;
    try {
      const text = await readClipboardText();
      if (menuTokenRef.current !== token) return;
      // term.paste 不经过按键：xterm 直接 triggerDataEvent 把文本交给 PTY（程序开了
      // bracketed paste 时会包上 ESC[200~/ESC[201~）。cmd 的 ^V、readline 的 quoted-insert
      // 这些抢 Ctrl+V 的绑定因此根本不会被触发，四个 shell 表现一致。
      termRef.current?.paste(text);
      closeMenu();
    } catch {
      if (menuTokenRef.current !== token) return;
      fail("读不到剪贴板，检查应用是否有读取剪贴板的权限");
    }
  }

  async function copySelection() {
    const token = menuTokenRef.current;
    const text = termRef.current?.getSelection() ?? "";
    if (!text) return;
    try {
      await writeClipboardText(text);
      if (menuTokenRef.current !== token) return;
      closeMenu();
    } catch {
      if (menuTokenRef.current !== token) return;
      fail("写不进剪贴板");
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-line px-2">
        <label className="flex items-center gap-1 text-[12px] text-muted">
          Shell
          <select
            className="h-7 rounded-md border border-line bg-bg px-1.5 text-[12px] text-ink"
            value={shell}
            onChange={(event) => void switchShell(event.target.value)}
          >
            {shells.length ? null : <option value={shell}>{shell}</option>}
            {shells.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <span className="min-w-0 flex-1 truncate text-[11px] text-faint" title={cwd}>
          {status === "connecting" ? "正在连接" : null}
          {status === "open" ? cwd : null}
          {status === "closed"
            ? `终端已退出${exitCode === null ? "" : `（代码 ${exitCode}）`}`
            : null}
          {status === "gone" ? "这个终端已经不在服务端了" : null}
          {status === "error" ? error || "连接失败" : null}
        </span>
        {status === "gone" || status === "closed" ? (
          <button
            type="button"
            className="h-7 shrink-0 rounded-md border border-line px-2 text-[12px] text-ink hover:bg-surface"
            onClick={() => recreateRef.current?.()}
          >
            重新创建
          </button>
        ) : null}
      </div>
      <div
        ref={hostRef}
        className="terminal-host min-h-0 flex-1 overflow-hidden px-1 py-1"
        onContextMenu={onContextMenu}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
      />
      {menu ? (
        <TerminalMenu
          x={menu.x}
          y={menu.y}
          hint={menu.hint}
          hasSelection={menu.hasSelection}
          onPaste={() => void pasteFromClipboard()}
          onCopy={() => void copySelection()}
          onSelectAll={() => {
            termRef.current?.selectAll();
            closeMenu();
          }}
          onClear={() => {
            // Ctrl+L：交给 shell 自己清，保留历史与提示符。
            sendRef.current?.("\x0c");
            closeMenu();
          }}
          onClose={dismissMenu}
        />
      ) : null}
      {bar ? (
        <SelectionBar
          x={bar.x}
          y={bar.y}
          copied={barCopied}
          onCopy={() => void copyBarText()}
          onQuote={quoteBarText}
          onDismiss={dismissBar}
        />
      ) : null}
    </div>
  );
}

/**
 * 终端选区的悬浮条：复制 / 添加到对话，和主对话里模型回复选中后的那条一个样子。
 *
 * 挂在 body 上（终端容器 overflow-hidden），锚点是最后一次指针位置；宽度按最宽的
 * 「添加到对话」预留，贴右边时整条翻到指针左侧。
 * onPointerDown 阻止默认：不然点一下菜单，终端就失焦，用户接着想敲键盘没反应。
 */
function SelectionBar({
  x,
  y,
  copied,
  onCopy,
  onQuote,
  onDismiss,
}: {
  x: number;
  y: number;
  copied: boolean;
  onCopy: () => void;
  onQuote: () => void;
  onDismiss: () => void;
}) {
  const barRef = useRef<HTMLDivElement | null>(null);
  const [style, setStyle] = useState<{ left: number; top: number }>({ left: x, top: y });

  useLayoutEffect(() => {
    const node = barRef.current;
    if (!node) return;
    const margin = 8;
    const width = node.offsetWidth;
    const height = node.offsetHeight;
    const left = x + width + margin > window.innerWidth ? Math.max(margin, x - width) : x;
    const top = y + height + margin > window.innerHeight ? Math.max(margin, y - height) : y;
    setStyle({ left: Math.max(margin, left), top });
  }, [copied, x, y]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      onDismiss();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onDismiss]);

  return createPortal(
    <div
      ref={barRef}
      role="toolbar"
      aria-label="终端选中文字"
      className="fixed z-40 flex items-center gap-0.5 rounded-lg border border-line bg-bg p-1 shadow-[0_8px_24px_var(--shadow-color)]"
      style={{ left: style.left, top: style.top }}
      onPointerDown={(event) => event.preventDefault()}
    >
      <button
        type="button"
        className="flex items-center gap-1 rounded-md px-2 py-1 text-[13px] text-ink hover:bg-hover"
        onClick={onCopy}
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        {copied ? "已复制" : "复制"}
      </button>
      <button
        type="button"
        className="flex items-center gap-1 rounded-md bg-active px-2 py-1 text-[13px] text-ink hover:bg-hover"
        onClick={onQuote}
      >
        <Reply className="size-3.5" />
        添加到对话
      </button>
    </div>,
    document.body,
  );
}

/**
 * 终端右键菜单。
 *
 * xterm 6 自己只借 contextmenu 做"右键选词"，没有菜单，所以这块自己画。挂在 body 上：
 * 终端容器是 `overflow-hidden`，菜单放里面会被裁掉。
 * 贴边时按实测尺寸翻到指针的另一侧，翻不动就贴回视口内 8px。
 */
function TerminalMenu({
  x,
  y,
  hint,
  hasSelection,
  onPaste,
  onCopy,
  onSelectAll,
  onClear,
  onClose,
}: {
  x: number;
  y: number;
  hint: string | null;
  hasSelection: boolean;
  onPaste: () => void;
  onCopy: () => void;
  onSelectAll: () => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [style, setStyle] = useState<{ left: number; top: number }>({ left: x, top: y });

  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const margin = 8;
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const left = x + width + margin > window.innerWidth ? Math.max(margin, x - width) : x;
    const top = y + height + margin > window.innerHeight ? Math.max(margin, y - height) : y;
    setStyle({ left: Math.max(margin, left), top });
  }, [hint, x, y]);

  useEffect(() => {
    function onPointer(event: PointerEvent) {
      if (panelRef.current?.contains(event.target as Node)) return;
      onClose();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("blur", onClose);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", onClose);
    };
  }, [onClose]);

  const items: { label: string; disabled?: boolean; run: () => void }[] = [
    { label: "粘贴", run: onPaste },
    { label: "复制", disabled: !hasSelection, run: onCopy },
    { label: "全选", run: onSelectAll },
    { label: "清屏", run: onClear },
  ];

  return createPortal(
    <div
      ref={panelRef}
      style={{ position: "fixed", left: style.left, top: style.top, zIndex: 90 }}
      className="min-w-[9.5rem] rounded-xl border border-line bg-bg p-1 shadow-[0_12px_32px_var(--shadow-color)]"
      onContextMenu={(event) => event.preventDefault()}
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          disabled={item.disabled}
          className="flex w-full items-center justify-between gap-4 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink hover:bg-hover disabled:pointer-events-none disabled:text-faint"
          onClick={item.run}
        >
          <span>{item.label}</span>
        </button>
      ))}
      {hint ? <p className="border-t border-line/70 px-2.5 py-1.5 text-[11px] leading-4 text-danger">{hint}</p> : null}
    </div>,
    document.body,
  );
}
