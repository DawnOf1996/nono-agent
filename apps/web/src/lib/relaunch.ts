/**
 * 重启应用。
 *
 * 换过数据目录之后只有重启才生效：数据目录在进程启动时解析一次（见
 * `nono_agent/paths`），会话事件流、app.db 和托管运行时都被当前进程占着，
 * 运行期热切换只会留下写坏的文件。
 *
 * 渲染进程没有重启自己的权限，只能经 preload 桥落到主进程的 `app.relaunch()`。
 * 浏览器里没有这个桥（Vite dev、纯 web），返回 false 让调用方提示用户手动重开。
 */
export async function relaunchApp(): Promise<boolean> {
  const bridge = typeof window !== "undefined" ? window.nonoDesktop?.relaunch : undefined;
  if (!bridge) return false;
  await bridge();
  return true;
}

/** 这次运行有没有能力自己重启（决定文案是「立即重启」还是「请手动重启应用」）。 */
export function canRelaunch(): boolean {
  return typeof window !== "undefined" && Boolean(window.nonoDesktop?.relaunch);
}
