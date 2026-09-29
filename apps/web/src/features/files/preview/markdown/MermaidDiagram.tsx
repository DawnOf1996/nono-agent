import { Children, isValidElement, useEffect, useId, useState, type ReactNode } from "react";

import { FigureCard, FigurePlaceholder } from "@/components/FigureCard";
import { sanitizeMermaidSvg } from "@/features/files/preview/markdown/svg";

let mermaidReady: Promise<typeof import("mermaid").default> | null = null;

function loadMermaid() {
  mermaidReady ??= import("mermaid").then((module) => {
    module.default.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      htmlLabels: false,
      theme: "neutral",
    });
    return module.default;
  });
  return mermaidReady;
}

let renderSeq = 0;

/** mermaid 默认把测量节点插进 body 的普通流，会把整页撑高并闪出最外层滚动条。 */
function renderOffscreen(mermaid: Awaited<ReturnType<typeof loadMermaid>>, id: string, source: string) {
  const host = document.createElement("div");
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position:fixed;left:0;top:0;visibility:hidden;pointer-events:none";
  document.body.appendChild(host);
  return mermaid.render(id, source, host).finally(() => host.remove());
}

export function mermaidBlockSource(children: ReactNode): string | null {
  const child = Children.toArray(children)[0];
  if (!isValidElement<{ className?: string; children?: ReactNode }>(child)) return null;
  const className = child.props.className ?? "";
  if (!/\blanguage-mermaid\b/i.test(className)) return null;
  return String(child.props.children ?? "").replace(/\n$/, "");
}

export function MermaidDiagram({ code }: { code: string }) {
  const reactId = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const source = code.trim();

  useEffect(() => {
    if (!source) return;
    let live = true;
    setError(null);
    void loadMermaid()
      .then(async (mermaid) => {
        renderSeq += 1;
        const rendered = await renderOffscreen(mermaid, `mermaid-${reactId}-${renderSeq}`, source);
        const clean = sanitizeMermaidSvg(rendered.svg);
        if (!clean) throw new Error("图表未通过安全检查");
        if (live) setSvg(clean);
      })
      .catch((cause: unknown) => {
        if (!live) return;
        setSvg(null);
        setError(cause instanceof Error ? cause.message : "无法渲染图表");
      });
    return () => {
      live = false;
    };
  }, [reactId, source]);

  if (!source) return null;
  if (error) {
    return <FigureCard kind="mermaid" svg={null} source={source} error="Mermaid 图表无法渲染" detail={error} />;
  }
  if (!svg) return <FigurePlaceholder label="正在渲染图表" />;
  return <FigureCard kind="mermaid" svg={svg} source={source} />;
}
