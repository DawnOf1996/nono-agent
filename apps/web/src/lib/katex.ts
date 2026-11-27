/**
 * KaTeX 的开关，聊天和文件预览共用一份。
 *
 * trust 必须显式关着：文件预览里 rehypeKatex 排在 sanitize **之后**，
 * 它吐出来的 HTML 不再经过白名单，全靠这道闸。trust 关着的时候
 * \href / \url / \includegraphics / \htmlClass 这些会生成 HTML 的命令一律报错，
 * 不会拼进 DOM；一旦有人改成 true，公式就成了绕过 sanitize 的注入口。
 *
 * 公式写错了 KaTeX 自己会画成红色错误样式，不会抛异常炸掉整条消息
 * （rehype-katex 内部兜住了，不用在这儿设 throwOnError）。
 */
export const KATEX_OPTIONS = {
  trust: false,
  strict: "ignore",
} as const;