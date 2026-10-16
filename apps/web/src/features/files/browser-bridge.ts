/**
 * 渲染进程侧的浏览器通道：接住引擎下发的命令，落到面板 store 上，再把回执送回去。
 *
 * 只有桌面端才连这条流 —— **连接本身就是「桌面端在场」的信号**，引擎的
 * browser_* 工具据此判断能不能工作。web 版不连，工具就会在几百毫秒内给出明确
 * 提示，而不是对着一个永远不会来的回执干等。
 *
 * 目前只实现「标签页从哪来」这一件事（开标签 / 列标签）。真正在网页里读结构、
 * 点元素的部分由 agent-browser 的 CLI 承担，见 sidecar 的
 * `nono_agent/tools/builtin/browser.py`。
 */

import { sendBrowserReply, watchBrowser, type BrowserCommand, type BrowserReplyBody } from "@/api/client";
import { usePanelStore } from "@/features/files/panel-store";
import { isDesktop } from "@/lib/desktop";

type BrowserTabInfo = { tabId: string; title: string; url: string };

function listBrowserTabs(): BrowserTabInfo[] {
  return usePanelStore
    .getState()
    .tabs.filter((tab) => tab.kind === "browser")
    .map((tab) => ({ tabId: tab.id, title: tab.title, url: tab.url }));
}

async function handleCommand(command: BrowserCommand): Promise<void> {
  let reply: BrowserReplyBody;
  try {
    if (command.op === "open") {
      // openBrowser 会把右侧栏展开并把新标签设为当前，用户能立刻看见。
      // 失败要如实回报，不能吞掉 —— 模型需要知道标签到底开没开。
      const tabId = usePanelStore.getState().openBrowser(command.url);
      reply = { request_id: command.request_id, ok: true, tab_id: tabId, url: command.url ?? "" };
    } else if (command.op === "tabs") {
      reply = { request_id: command.request_id, ok: true, tabs: listBrowserTabs() };
    } else {
      reply = {
        request_id: command.request_id,
        ok: false,
        message: `桌面端不认识的操作：${command.op}`,
      };
    }
  } catch (error) {
    reply = {
      request_id: command.request_id,
      ok: false,
      message: error instanceof Error ? error.message : String(error),
    };
  }

  // 回执送不回去也只记日志：请求已经在引擎侧超时或断连了，重试没有意义。
  try {
    await sendBrowserReply(reply);
  } catch (error) {
    console.warn("[browser] 回执发送失败", command.op, error);
  }
}

/**
 * 启动浏览器通道，返回停掉它的函数。
 *
 * 在 web 版上直接返回一个空函数：那边没有 `<webview>`，连上去也没有意义，
 * 而且会让引擎误以为桌面端在场。
 */
export function startBrowserBridge(): () => void {
  if (!isDesktop()) return () => {};
  const controller = new AbortController();
  void watchBrowser(controller.signal, {
    onCommand: handleCommand,
    onOpen: () => console.info("[browser] 通道已连接"),
    onClose: () => console.info("[browser] 通道已断开"),
  });
  return () => controller.abort();
}
