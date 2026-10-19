/**
 * 用户中止标记 —— 前后端共享的一份契约。
 *
 * 单独成文件而不是塞在 sse.ts 里，有两个原因：
 *
 * 1. 它跨边界。后端 `nono_agent/core/loop.py` 的 `ABORT_MARKER` 和这里必须逐字一致，
 *    对不上就会漏出裸标记给用户看。放在一个只干这一件事的地方，比埋在几百行的
 *    事件解析代码里好找。
 * 2. 它得能单测。sse.ts 顶上挂着 `@/` 别名依赖，node 的 test runner 解析不了，
 *    逻辑藏在那里就等于没有测试。
 */

/**
 * 后端在「用户按下停止」时追加在回复末尾的标记块（单独一个 text block）。
 * 必须与 `nono_agent/core/loop.py` 的 `ABORT_MARKER` 逐字一致。
 *
 * 它是给**模型**看的：让下一轮知道上一段是被打断的，而不是自己想说完的。
 * 但用户已经读过上面那段原文了，再原样显示一遍纯属噪音 —— 所以界面把它摘掉，
 * 换成一枚安静的「已停止」提示。
 */
export const ABORT_MARKER = "[用户已取消本次请求，上面的回复未完成]";

/** 摘掉末尾的中止标记，并报告这段是不是被打断的。 */
export function stripAbortMarker(text: string): { text: string; aborted: boolean } {
  if (!text.includes(ABORT_MARKER)) return { text, aborted: false };
  return {
    text: text.replace(ABORT_MARKER, "").trim(),
    aborted: true,
  };
}
