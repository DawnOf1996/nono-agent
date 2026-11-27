import DOMPurify from "dompurify";

const STRIP_ELEMENTS = new Set([
  "foreignobject",
  "script",
  "img",
  "iframe",
  "object",
  "embed",
  "video",
  "audio",
  "input",
  "button",
  "form",
  "link",
  "meta",
  "base",
]);

/** mermaid 的 strict 模式之后再洗一遍 SVG：去掉脚本、外来 HTML、事件属性和全部链接。 */
export function sanitizeMermaidSvg(svg: string): string {
  const purified = DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true, svgFilters: true },
  });
  if (!purified || typeof DOMParser === "undefined") return "";
  let doc: Document;
  try {
    doc = new DOMParser().parseFromString(purified, "image/svg+xml");
  } catch {
    return "";
  }
  if (doc.querySelector("parsererror") !== null) return "";
  const root = doc.documentElement;
  if (root === null || root.localName.toLowerCase() !== "svg") return "";
  for (const node of [...root.querySelectorAll("*")]) {
    if (STRIP_ELEMENTS.has(node.localName.toLowerCase())) {
      node.remove();
      continue;
    }
    for (const attribute of [...node.attributes]) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on") || name.startsWith("@") || name === "href" || name.endsWith(":href")) {
        node.removeAttribute(attribute.name);
      }
    }
  }
  return new XMLSerializer().serializeToString(root);
}

const CJK_FONT = '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif';

function withReadableFont(root: Element) {
  const style = root.getAttribute("style") ?? "";
  if (/font-family\s*:/i.test(style)) {
    if (/yahei|pingfang|noto sans sc|source han|微软雅黑/i.test(style)) return;
    root.setAttribute(
      "style",
      style.replace(/font-family\s*:\s*([^;]+)/i, (_, fonts: string) => `font-family: ${fonts.trim()}, ${CJK_FONT}`),
    );
    return;
  }
  const glue = style && !style.trim().endsWith(";") ? ";" : "";
  root.setAttribute("style", `${style}${glue}font-family: ${CJK_FONT}`);
}

function uniquifyIds(root: Element, prefix: string) {
  const names = new Map<string, string>();
  for (const node of [root, ...root.querySelectorAll("[id]")]) {
    const id = node.getAttribute("id");
    if (!id || names.has(id)) continue;
    const next = `${prefix}${id.replace(/[^\w-]/g, "")}`;
    names.set(id, next);
    node.setAttribute("id", next);
  }
  if (!names.size) return;
  const rewrite = (value: string) =>
    value.replace(/url\(\s*["']?#([^"')]+)["']?\s*\)/g, (match, id: string) => {
      const next = names.get(id);
      return next ? `url(#${next})` : match;
    });
  for (const node of [root, ...root.querySelectorAll("*")]) {
    for (const attribute of [...node.attributes]) {
      const next = rewrite(attribute.value);
      if (next !== attribute.value) node.setAttribute(attribute.name, next);
    }
  }
}

/**
 * 清洗模型给出的 SVG，并把 viewBox 扩到内容外框。
 * 对话栏很窄，裁切过的 viewBox 会把右侧和底部的节点切掉。
 */
export function prepareChatSvg(markup: string, idPrefix: string): string {
  const clean = sanitizeMermaidSvg(markup);
  if (!clean || typeof document === "undefined") return clean;
  const parsed = new DOMParser().parseFromString(clean, "image/svg+xml");
  if (parsed.querySelector("parsererror") !== null) return "";
  const root = parsed.documentElement;
  if (root.localName.toLowerCase() !== "svg") return "";
  const imported = document.importNode(root, true);
  if (!(imported instanceof SVGSVGElement)) return "";
  const graphic = imported;
  uniquifyIds(graphic, idPrefix);
  withReadableFont(graphic);

  const host = document.createElement("div");
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position:absolute;left:0;top:0;opacity:0;pointer-events:none;z-index:-1";
  if (!graphic.getAttribute("width")) graphic.setAttribute("width", "800");
  if (!graphic.getAttribute("height")) graphic.setAttribute("height", "800");
  host.appendChild(graphic);
  document.body.appendChild(host);
  let box: DOMRect | null = null;
  try {
    box = graphic.getBBox();
  } catch {
    box = null;
  }
  host.remove();

  if (box && box.width > 0 && box.height > 0) {
    const pad = Math.max(16, Math.max(box.width, box.height) * 0.04);
    const x = box.x - pad;
    const y = box.y - pad;
    const width = box.width + pad * 2;
    const height = box.height + pad * 2;
    graphic.setAttribute("viewBox", `${x} ${y} ${width} ${height}`);
    graphic.setAttribute("width", String(Math.ceil(width)));
    graphic.setAttribute("height", String(Math.ceil(height)));
  }
  graphic.setAttribute("preserveAspectRatio", "xMidYMid meet");
  return new XMLSerializer().serializeToString(graphic);
}
