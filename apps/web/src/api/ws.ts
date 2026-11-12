/**
 * 常驻长连接的 WebSocket 通道。
 *
 * **为什么不用 SSE**：浏览器对同一 origin 的 HTTP/1.1 并发连接有硬上限
 * （Chromium 写死 6 条，见 ``net/socket/client_socket_pool_manager.cc``）。
 * 应用常驻着好几条长连接就把名额占满，普通请求（文件树、聊天）只能排队 ——
 * 用户看到的就是「一调某个工具，整个界面全卡住」。
 *
 * WebSocket 在 Chromium 里走**独立的 socket pool**（每组上限 255），不跟普通
 * 请求抢那几条名额。
 *
 * **帧格式不变**：服务端仍按 SSE 的写法发帧（``event: x\ndata: {...}\n\n``），
 * 换的只是承载它的连接 —— 客户端那套帧解析器照用。
 */

import { parseSseText, type SseFrame } from "@/api/sse";

/** 断线后的重连等待：让服务端有时间重启，又不至于让界面明显迟到。 */
const RECONNECT_MS = 1000;

/**
 * sidecar 的 WebSocket 地址。
 *
 * 开发态直连 sidecar，不走 Vite 代理 —— 代理那条路在开发时容易被浏览器挂起。
 * 打包态页面本身就是从 sidecar 加载的，同源即可。
 */
export function sidecarWsUrl(path: string): string {
  let base = window.location.origin;
  if (import.meta.env.DEV) {
    try {
      base = __NONO_SIDECAR_HTTP__;
    } catch {
      base = "http://127.0.0.1:8000";
    }
  }
  const url = new URL(path, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export type EventSocketHandlers = {
  /** 收到一帧。**按到达顺序串行调用**，前一个没处理完不会处理下一个。 */
  onFrame: (frame: SseFrame) => void | Promise<void>;
  /** 连接建起来（含每次重连成功）时叫一次。 */
  onOpen?: () => void;
  /** 连接断开时叫一次。 */
  onClose?: () => void;
};

/**
 * 打开一条常驻的帧流，断线自动重连。
 *
 * 返回的函数用来关掉它 —— 之后不再重连。
 */
export function openEventSocket(
  url: string,
  handlers: EventSocketHandlers,
  signal?: AbortSignal,
): () => void {
  let socket: WebSocket | null = null;
  let timer: number | null = null;
  let stopped = false;

  const stop = () => {
    stopped = true;
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    const current = socket;
    socket = null;
    current?.close();
  };

  const schedule = () => {
    if (stopped || signal?.aborted || timer !== null) return;
    timer = window.setTimeout(() => {
      timer = null;
      connect();
    }, RECONNECT_MS);
  };

  const connect = () => {
    if (stopped || signal?.aborted) return;
    let next: WebSocket;
    try {
      next = new WebSocket(url);
    } catch {
      // 地址构造失败这类硬错误，重连也没用 —— 但仍要按断开处理，让上层知道。
      handlers.onClose?.();
      return;
    }
    socket = next;
    // 帧按到达顺序串行处理：浏览器通道的命令是「一问一答」，乱序执行会打架。
    let pending: Promise<void> = Promise.resolve();

    next.onopen = () => {
      handlers.onOpen?.();
    };
    next.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      for (const frame of parseSseText(event.data)) {
        // 单帧处理失败不能打断整条链：后面的帧照收，只把错误记下来。
        pending = pending
          .then(() => handlers.onFrame(frame))
          .catch((error) => {
            console.error("[ws] 帧处理失败", error);
          });
      }
    };
    next.onerror = () => {
      // 具体错误信息拿不到（浏览器不给），统一交给 onclose 处理重连。
      next.close();
    };
    next.onclose = () => {
      if (socket === next) socket = null;
      handlers.onClose?.();
      schedule();
    };
  };

  signal?.addEventListener("abort", stop, { once: true });
  connect();
  return stop;
}
