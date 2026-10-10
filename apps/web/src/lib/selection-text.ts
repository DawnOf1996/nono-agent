/**
 * 选区 → 引用文本。公式还原成 LaTeX 源码。
 *
 * 为什么不能直接用 selection.toString()：KaTeX 把一个公式渲染成**两棵树** ——
 * `.katex-mathml`（clip 成 1px 的 MathML，给读屏软件用，里面还藏着 <annotation> 的 TeX 原文）
 * 和 `.katex-html`（人看的字形）。两棵都在 DOM 里，也都可选中，
 * toString() 会把两棵的文字全抄一遍：选一个 $E = mc^2$ 出来是
 * "E=mc2E = mc^2E=mc2" 这种东西 —— 既不是人读的，也不是模型能用的。
 *
 * 办法：cloneContents() 拿一份选区的 DOM 副本，把副本里每个 .katex 换成
 * <annotation> 里的 LaTeX（按块级/行内补上 $ / $$ 包裹），再读 textContent。
 * 跨着公式拖选也能拼对，正文照常保留。
 */

/** 一个 .katex 元素里那份 TeX 原文。KaTeX 放在 MathML 的 <annotation> 里。 */
export function katexSourceOf(katex: {
  querySelector(selectors: string): { textContent: string | null } | null;
}): string | null {
  const tex = katex.querySelector('annotation[encoding="application/x-tex"]')?.textContent?.trim() ?? "";
  return tex === "" ? null : tex;
}

/** 补回定界符，模型读到的就是原始 Markdown 片段。 */
export function wrapMath(tex: string, display: boolean): string {
  return display ? `$$\n${tex}\n$$` : `$${tex}$`;
}

/** 一个 .katex 是不是块级（外面裹着 .katex-display）。必须在**原树**上问，副本里 closest 会失效。 */
export function isDisplayMath(katex: Element): boolean {
  return katex.closest(".katex-display") !== null;
}

/** 范围内按文档顺序列出所有 .katex。 */
function collectKatex(scope: Element | null): Element[] {
  if (scope === null) return [];
  const out: Element[] = [];
  // scope 本身也可能就是那个 .katex（选区整个落在公式里时会走上面那条分支，
  // 但取不到源码时会落到这里），querySelectorAll 只找后代，漏掉自己就整体错位一位。
  if (scope.matches(".katex")) out.push(scope);
  for (const node of scope.querySelectorAll<Element>(".katex")) out.push(node);
  return out;
}

/**
 * 选区引用文本：公式还原成 LaTeX，其余原样。
 *
 * 两种情况分开走：
 *   一、选区整个落在某个公式里（含 commonAncestorContainer 本身就是 .katex）。
 *      只框了公式一部分时，clone 出来的碎片未必还认得出 .katex，
 *      这时按"所在的公式"整个还原，比抄一份字形乱码强。
 *   二、选区跨着若干公式和正文。逐个把副本里的 .katex 换成源码，正文原样保留。
 */
export function selectionText(range: Range): string {
  const container = range.commonAncestorContainer;
  // 用 nodeType 而不是 instanceof：测试里 jsdom 的元素和全局 Element 不同源，
  // instanceof 恒为 false。1 === ELEMENT_NODE。
  const scope = container.nodeType === 1 ? (container as Element) : container.parentElement;

  const enclosing = scope?.closest(".katex") ?? null;
  if (enclosing !== null) {
    const tex = katexSourceOf(enclosing);
    if (tex !== null) return wrapMath(tex, isDisplayMath(enclosing));
  }

  const originals = collectKatex(scope).filter((node) => range.intersectsNode(node));
  const fragment = range.cloneContents();
  const owner = fragment.ownerDocument;
  fragment.querySelectorAll<Element>(".katex").forEach((clone, index) => {
    const tex = katexSourceOf(clone);
    if (tex === null) {
      // 只框到半个公式，副本里 annotation 也是断的。整段丢掉 ——
      // 抄一份 MathML 乱码递进对话，比不抄更糟。
      clone.remove();
      return;
    }
    const original = originals[index];
    clone.replaceWith(owner.createTextNode(wrapMath(tex, original !== undefined && isDisplayMath(original))));
  });

  return (fragment.textContent ?? "").replace(/\u00a0/g, " ");
}