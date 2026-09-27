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
