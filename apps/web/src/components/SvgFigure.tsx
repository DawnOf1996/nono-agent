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

export function SvgFigure({ markup }: { markup: string }) {
  const reactId = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useLayoutEffect(() => {
    const next = prepareChatSvg(markup, `chat-svg-${reactId}-`);
    setFailed(!next);
    setSvg(next || null);
  }, [markup, reactId]);

  if (failed) {
    return <FigureCard kind="svg" svg={null} source={markup} error="SVG 无法渲染" />;
  }
  if (!svg) return <FigurePlaceholder label="正在渲染图形" />;
  return <FigureCard kind="svg" svg={svg} source={markup} />;
}
