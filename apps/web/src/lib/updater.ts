/**
 * 应用更新的渲染侧桥接。
 *
 * 和 `lib/relaunch.ts` 是同一个思路：渲染进程不碰网络也不碰文件系统，
 * 更新这件事整个住在主进程，这里只做两件事 —— 把主进程推来的事件流收拢成一个
 * 状态机，以及回答「当前环境能不能更新」。
 *
 * 能不能更新要过两关：
 * 1. preload 有没有暴露这几个方法（Vite dev、纯 web 打开都没有）；
 * 2. 主进程是不是打包态。开发态没有 app-update.yml —— 那是 electron-builder
 *    打进安装包里的，硬查必然失败，main.js 会回 `{ ok: false, reason: "dev" }`。
 *
 * 状态机本身是纯函数（reduceUpdateEvent），抽出来是为了能在不启动 Electron 的
 * 前提下单测，也为了让设置页里不用堆一堆 useState。
 */

/** 设置页那一行能处于的全部状态。 */
export type UpdateState =
  /** 还没查过，等用户点「检查更新」。 */
  | { kind: "idle" }
  /** 正在查。 */
  | { kind: "checking" }
  /** 查完了，当前就是最新。 */
  | { kind: "latest"; version: string | null }
  /** 有新版，还没开始下载。 */
  | { kind: "available"; version: string }
  /** 正在下载，percent 是 0~100 的整数。 */
  | { kind: "downloading"; percent: number }
  /** 下载完了，等用户点「重启并安装」。 */
  | { kind: "downloaded"; version: string }
  /** 这个环境没法更新（浏览器里打开、或 Electron 开发态）。 */
  | { kind: "unsupported" }
  /** 查或下载失败。多数是断网。 */
  | { kind: "error"; message: string };

export const INITIAL_UPDATE_STATE: UpdateState = { kind: "idle" };

/** 状态机。纯函数 —— 不碰 window，也不碰主进程。 */
export function reduceUpdateEvent(state: UpdateState, event: NonoUpdateEvent): UpdateState {
  switch (event.type) {
    case "checking":
      return { kind: "checking" };
    case "available":
      return { kind: "available", version: event.version };
    case "latest":
      return { kind: "latest", version: event.version };
    case "progress":
      // 下载进度不该把已有的错误态冲掉，但也不该让「已下完」退回「还在下」：
      // updater 在收尾阶段还会再吐几个 percent=100 的事件。
      if (state.kind === "downloaded") return state;
      return { kind: "downloading", percent: event.percent };
    case "downloaded":
      return { kind: "downloaded", version: event.version };
    case "error":
      return { kind: "error", message: event.message };
    default:
      return state;
  }
}

/** 这个环境有没有更新的能力（决定按钮是显示还是干脆不显示）。 */
export function canCheckForUpdates(): boolean {
  return typeof window !== "undefined" && Boolean(window.nonoDesktop?.checkForUpdate);
}

/** 订阅主进程的更新事件流，返回退订函数。 */
export function subscribeUpdateEvents(handler: (event: NonoUpdateEvent) => void): () => void {
  const bridge = typeof window !== "undefined" ? window.nonoDesktop?.onUpdateEvent : undefined;
  if (!bridge) return () => {};
  return bridge(handler);
}

/**
 * 手动触发一次检查。autoDownload 是关着的，所以这里最多把状态推到
 * `available`，不会开始下载。
 */
export async function checkForUpdate(): Promise<UpdateState> {
  const bridge = typeof window !== "undefined" ? window.nonoDesktop?.checkForUpdate : undefined;
  if (!bridge) return { kind: "unsupported" };
  const result = await bridge();
  if (result.ok) return { kind: "checking" };
  // reason:"dev" 是主进程主动说「这里不查」，不是失败 —— 文案要区别对待，
  // 不然开发态每次开设置都顶着一句红色的「检查失败」。
  if (result.reason === "dev") return { kind: "unsupported" };
  return { kind: "error", message: result.error || "检查失败" };
}

/** 下载已发现的新版。真实进度走事件流，这里只回执有没有启动成功。 */
export async function downloadUpdate(): Promise<boolean> {
  const bridge = typeof window !== "undefined" ? window.nonoDesktop?.downloadUpdate : undefined;
  if (!bridge) return false;
  return (await bridge()).ok;
}

/** 装下已下载的更新并重启。返回 false 表示没能启动安装流程，应用还开着。 */
export async function installUpdate(): Promise<boolean> {
  const bridge = typeof window !== "undefined" ? window.nonoDesktop?.installUpdate : undefined;
  if (!bridge) return false;
  return (await bridge()).ok;
}