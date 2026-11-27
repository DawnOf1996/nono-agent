import { useEffect } from "react";

import { usePanelStore } from "@/features/files/panel-store";
import { resolveLinkTarget } from "@/lib/link-target";

/**
 * 全应用统一的链接打开方式。
 *
 * 桌面版把应用装在一个 <webview> 里，链接只要带上 target=_blank（或者 onClick 里
 * 不 preventDefault），Electron 就会另开一个原生窗口 —— 用户点的只是一个地址，
 * 整个应用却被顶到任务栏后面去了。
 *
 * 所以这里在根节点收一层点击代理：正文里能点的地址一律开成侧边栏标签 —— 网址开
 * 浏览器，文件路径开文件查看器（图片按后缀自动落到图片预览）—— 并且总是
 * preventDefault，绝不留给浏览器去新开窗口。
 *
 * 委托到根节点而不是给每个 <a> 挂 handler，有两个原因：
 *   - Markdown 在流式渲染时会反复重建子树，逐个绑定既费劲又容易漏。
 *   - 相对路径要按「这段正文所在文件」解析，只有容器知道自己是谁。容器在 DOM 上
 *     标一个 data-link-file，全局代理点击时往上找这一个属性就够了。
 */

/** 普通左键、没按修饰键，才算「我要打开这个链接」。 */
function isPlainLeftClick(event: MouseEvent): boolean {
  if (event.button !== 0) return false;
  return !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

/** 最近的祖先容器上的 data-link-file —— 这段正文所在文件的完整路径。 */
function fileOf(node: Element | null): string {
  return node?.closest?.("[data-link-file]")?.getAttribute("data-link-file") ?? "";
}

/**
 * 打开一个链接目标：网址开浏览器标签，文件路径开文件预览标签。
 *
 * 判不出来（越界路径、非法协议、页内锚点）时返回 false，调用方按「不接管」处理。
 */
export function openLinkTarget(
  href: string | undefined,
  options: { root?: string; fromFile?: string } = {},
): boolean {
  const store = usePanelStore.getState();
  const target = resolveLinkTarget(href, {
    root: options.root ?? store.root,
    fromFile: options.fromFile,
  });
  switch (target.kind) {
    case "web":
      store.openBrowser(target.url);
      return true;
    case "file":
      store.openFile(target.path);
      return true;
    default:
      return false;
  }
}

/**
 * 在一个容器上接管链接点击，返回解绑函数。
 *
 * 挂在根节点上就能覆盖对话区、侧边预览、侧边对话等所有渲染正文的地方；
 * 也可以单独挂某个容器（自己需要指定基准文件时）。
 */
export function attachLinkRouter(container: HTMLElement, options: { fromFile?: string } = {}): () => void {
  const onClick = (event: MouseEvent) => {
    // 修饰键组合与中键保持浏览器默认：用户明确要「新开窗口」时不拦。
    if (!isPlainLeftClick(event)) return;
    const node = event.target as HTMLElement | null;
    // 图表、表格、引用里都包了好几层，closest 才能从点到的子元素走到 <a>。
    const anchor = node?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!anchor) return;
    // 页内锚点留在原地，交给浏览器自己跳。
    if (anchor.hasAttribute("download")) return;
    const fromFile = options.fromFile ?? fileOf(anchor);
    if (!openLinkTarget(anchor.getAttribute("href") ?? undefined, { fromFile })) return;
    event.preventDefault();
    event.stopPropagation();
  };
  container.addEventListener("click", onClick);
  return () => container.removeEventListener("click", onClick);
}

/** 在应用根节点上挂全局代理。挂在 App 一处即可。 */
export function useGlobalLinkRouter(): void {
  useEffect(() => {
    if (!document.body) return;
    return attachLinkRouter(document.body);
  }, []);
}
