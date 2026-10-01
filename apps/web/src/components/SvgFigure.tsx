import { Children, isValidElement, useId, useLayoutEffect, useState, type ReactNode } from "react";

import { FigureCard, FigurePlaceholder } from "@/components/FigureCard";
import { prepareChatSvg } from "@/features/files/preview/markdown/svg";

function blockText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(blockText).join("");
  return "";
}

export function svgBlockSource(children: ReactNode): string | null {
  const child = Children.toArray(children)[0];
  if (!isValidElement<{ className?: string; children?: ReactNode }>(child)) return null;
  const className = child.props.className ?? "";
  if (!/\blanguage-svg\b/i.test(className)) return null;
  const source = blockText(child.props.children).replace(/\n$/, "").trim();
  if (!/^<svg[\s>]/i.test(source) || !/<\/svg>\s*$/i.test(source)) return null;
  return source;
}

const PREPARE_LIMIT = 32;
/** 同一段源码只做一次 DOMParser + getBBox：重复打开或组件重挂载时直接取结果。 */
const prepared = new Map<string, string>();

function prepareOnce(markup: string, idPrefix: string): string {
  const key = `${idPrefix}\u0000${markup}`;
  const hit = prepared.get(key);
  if (hit !== undefined) return hit;
  const result = prepareChatSvg(markup, idPrefix);
  if (prepared.size >= PREPARE_LIMIT) {
    const oldest = prepared.keys().next();
    if (!oldest.done) prepared.delete(oldest.value);
  }
  prepared.set(key, result);
  return result;
}

export function SvgFigure({ markup }: { markup: string }) {
  const reactId = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useLayoutEffect(() => {
    const next = prepareOnce(markup, `chat-svg-${reactId}-`);
    setFailed(!next);
    setSvg(next || null);
  }, [markup, reactId]);

  if (failed) {
    return <FigureCard kind="svg" svg={null} source={markup} error="SVG 无法渲染" />;
  }
  if (!svg) return <FigurePlaceholder label="正在渲染图形" />;
  return <FigureCard kind="svg" svg={svg} source={markup} />;
}
