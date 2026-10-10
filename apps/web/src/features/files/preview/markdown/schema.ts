/**
 * 文件预览的 rehype-sanitize 白名单。
 *
 * 单独拆出来是为了能直接测。这张表里有两条规则特别容易被无声改坏，
 * 而且坏了以后现象很偏（"代码块没高亮"、"公式不渲染"），不测就发现不了。
 */

import { defaultSchema } from "rehype-sanitize";

/**
 * 行号属性。**必须写 hast 的属性名 `dataLine`，不能写 HTML 里的 `data-line`。**
 *
 * hast 存属性一律是驼峰，DOM 上才是 `data-line`。而 hast-util-sanitize 的
 * findDefinition 是精确比对（`name === key`），写成 "data-line" 就永远匹配不上
 * `dataLine` —— 属性被整条剥掉，页面上一个 [data-line] 都不会有，
 * 选中文字引用时行号全是 null。
 *
 * 落库成 HTML 属性名由 hast-util-to-jsx-runtime 负责，出 DOM 就是 data-line，
 * SelectionQuote 那边读 dataset.line，不用改。
 */
export const LINE_ATTR = "dataLine";

/** defaultSchema 给某个标签的属性白名单条目。 */
type AttrDef = NonNullable<NonNullable<typeof defaultSchema>["attributes"]>[string][number];

/**
 * 在 defaultSchema 的白名单后面**追加**属性。
 *
 * 这里是追加而不是替换，有一条不能碰的红线：<code> 的白名单里有 className
 * （限 /^language-./），它就是围栏语言名的唯一来源。早先写成 code: [LINE_ATTR]
 * 把整条换掉，className 连同语言名在 sanitize 阶段一起被抹掉，
 * CodeFence 的 fenceMeta 拿不到 className，只能回落成 "text" ——
 * 侧边预览里所有代码块因此全成了纯文本，没有高亮。
 * ul/ol/li 同理，写死白名单会把 GFM 任务列表的 class 也弄丢。
 */
export function allow(tag: string, ...extra: AttrDef[]): AttrDef[] {
  return [...(defaultSchema.attributes?.[tag] ?? []), ...extra];
}

export const markdownSchema = {
  ...defaultSchema,
  tagNames: [...new Set([...(defaultSchema.tagNames ?? []), "div", "details", "summary", "center"])],
  attributes: {
    ...defaultSchema.attributes,
    div: allow("div", "align"),
    center: allow("center", "align"),
    img: allow("img", "src", "alt", "title", "width", "height", "align"),
    a: allow("a", "href", "title", "name"),
    details: allow("details", "open"),
    // 凡是能被选中文字的块级元素都要带上行号。
    p: allow("p", LINE_ATTR),
    h1: allow("h1", LINE_ATTR),
    h2: allow("h2", LINE_ATTR),
    h3: allow("h3", LINE_ATTR),
    h4: allow("h4", LINE_ATTR),
    h5: allow("h5", LINE_ATTR),
    h6: allow("h6", LINE_ATTR),
    blockquote: allow("blockquote", LINE_ATTR),
    ul: allow("ul", LINE_ATTR),
    ol: allow("ol", LINE_ATTR),
    li: allow("li", LINE_ATTR),
    pre: allow("pre", LINE_ATTR),
    // <code> 的 className 白名单必须合成一条，不能像上面那样拆成两条。
    // hast-util-sanitize 的 findDefinition 对同一个属性名只返回**第一条**定义
    // （见它的 lib/index.js），写成 [.., ['className', /^language-./]], ['className', 'math-inline']]
    // 的话第二条永远轮不到，math-inline / math-display 照样被剥掉，KaTeX 就找不到目标。
    // 默认值里本来就有的 /^language-./ 必须一并留下 —— ```ts 这类围栏靠它才有高亮。
    code: [["className", /^language-./, "math-inline", "math-display"], LINE_ATTR],
    table: allow("table", LINE_ATTR),
    tr: allow("tr", LINE_ATTR),
    td: allow("td", LINE_ATTR),
    th: allow("th", LINE_ATTR),
    hr: allow("hr", LINE_ATTR),
  },
};