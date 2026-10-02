import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import "@xterm/xterm/css/xterm.css";

import { fetchTerminalShells, startTerminal, stopTerminal, type TerminalShell } from "@/api/client";
import { usePanelStore } from "@/features/files/panel-store";
import { getSidebarPrefs, subscribeSidebarPrefs } from "@/features/settings/sidebar-prefs";
import { useResolvedTheme } from "@/lib/theme";

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

function terminalTheme() {
  const style = getComputedStyle(document.documentElement);
  const pick = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  return {
    background: pick("--color-bg", "#ffffff"),
    foreground: pick("--color-ink", "#0f1115"),
    cursor: pick("--color-ink", "#111827"),
    // 选区色也走 token：xterm 要的是实色 rgba，深浅两套的选区浓度不一样，写死一边另一边就看不见。
    selectionBackground: pick("--term-selection", "rgba(17, 24, 39, 0.16)"),
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
  const patchTerminal = usePanelStore((state) => state.patchTerminal);
  const [shells, setShells] = useState<TerminalShell[]>([]);
  const [status, setStatus] = useState<"connecting" | "open" | "restarted" | "closed" | "error">("connecting");
  const [error, setError] = useState<string | null>(null);
  const fontSize = useSyncExternalStore(subscribeSidebarPrefs, getSidebarPrefs, getSidebarPrefs).terminalFontSize;
  const theme = useResolvedTheme();

  // xterm 在构造时就把颜色烤进自己的画布，切主题不会自己跟着变，得重新喂一次。
  useEffect(() => {
    const term = termRef.current;
    if (term) term.options.theme = terminalTheme();
  }, [theme]);

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
    let socket: WebSocket | null = null;
    let disposed = false;
    let allowRestart = true;

    const sendResize = () => {
      if (host.clientWidth < 20 || host.clientHeight < 20) return;
      fit.fit();
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };

    const connect = (id: string) => {
      const next = new WebSocket(terminalWsUrl(id));
      next.binaryType = "arraybuffer";
      socket = next;
      next.onopen = () => {
        setStatus((current) => (current === "restarted" ? current : "open"));
        setError(null);
        sendResize();
        term.focus();
      };
      next.onmessage = (event) => {
        if (typeof event.data === "string") {
          try {
            const payload = JSON.parse(event.data) as { type?: string; code?: number };
            if (payload.type === "missing" && allowRestart) {
              allowRestart = false;
              void revive();
              return;
            }
            if (payload.type === "exit") setStatus("closed");
          } catch {
            term.write(event.data);
          }
          return;
        }
        term.write(new Uint8Array(event.data as ArrayBuffer));
      };
      next.onclose = (event) => {
        if (disposed) return;
        if (event.code === 4404 && allowRestart) {
          allowRestart = false;
          void revive();
          return;
        }
        setStatus((current) => (current === "restarted" ? current : "closed"));
      };
      next.onerror = () => {
        if (!disposed) setStatus("error");
      };
    };

    async function revive() {
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
        setStatus("restarted");
      } catch (cause) {
        if (!disposed) {
          setStatus("error");
          setError(cause instanceof Error ? cause.message : "无法重新连接终端");
        }
      }
    }

    const disposable = term.onData((data) => {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input", data }));
      }
    });
    connect(serverId);
    const observer = new ResizeObserver(() => sendResize());
    observer.observe(host);
    return () => {
      disposed = true;
      termRef.current = null;
      disposable.dispose();
      observer.disconnect();
      socket?.close();
      term.dispose();
    };
  }, [cwd, fontSize, patchTerminal, root, serverId, shell, tabId]);

  useEffect(() => {
    if (!active) return;
    termRef.current?.focus();
    const host = hostRef.current;
    if (!host) return;
    host.dispatchEvent(new Event("resize"));
  }, [active]);

  async function switchShell(nextShell: string) {
    if (nextShell === shell) return;
    const chosen = shells.find((item) => item.id === nextShell);
    setStatus("connecting");
    setError(null);
    await stopTerminal(serverId).catch(() => undefined);
    try {
      const created = await startTerminal({ root, cwd, shell: nextShell, cols: 80, rows: 24 });
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
          {status === "restarted" ? "已重新连接" : null}
          {status === "closed" ? "终端已退出" : null}
          {status === "error" ? error || "连接失败" : null}
          {status === "open" ? cwd : null}
        </span>
      </div>
      <div ref={hostRef} className="terminal-host min-h-0 flex-1 overflow-hidden px-1 py-1" />
    </div>
  );
}
