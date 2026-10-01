import { Children, isValidElement, useEffect, useState, type ReactNode } from "react";

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

const CACHE_LIMIT = 64;
/** 源码 → 渲染结果。同一段源码只画一次：重复打开、组件重挂载、同一篇里出现两次都直接复用。 */
const renderedCache = new Map<string, string>();
const inFlight = new Map<string, Promise<string>>();

function cachedDiagram(source: string): string | null {
  return renderedCache.get(source) ?? null;
}

function renderDiagram(source: string): Promise<string> {
  const hit = inFlight.get(source);
  if (hit) return hit;
  const task = loadMermaid()
    .then(async (mermaid) => {
      renderSeq += 1;
      const rendered = await renderOffscreen(mermaid, `mermaid-${renderSeq}`, source);
      const clean = sanitizeMermaidSvg(rendered.svg);
      if (!clean) throw new Error("图表未通过安全检查");
      if (renderedCache.size >= CACHE_LIMIT) {
        const oldest = renderedCache.keys().next();
        if (!oldest.done) renderedCache.delete(oldest.value);
      }
      renderedCache.set(source, clean);
      return clean;
    })
    .finally(() => inFlight.delete(source));
  inFlight.set(source, task);
  return task;
}

export function mermaidBlockSource(children: ReactNode): string | null {
  const child = Children.toArray(children)[0];
  if (!isValidElement<{ className?: string; children?: ReactNode }>(child)) return null;
  const className = child.props.className ?? "";
  if (!/\blanguage-mermaid\b/i.test(className)) return null;
  return String(child.props.children ?? "").replace(/\n$/, "");
}

export function MermaidDiagram({ code }: { code: string }) {
  const source = code.trim();
  // 首帧就命中缓存的话，连「正在渲染图表」都不会闪一下。
  const [svg, setSvg] = useState<string | null>(() => cachedDiagram(source));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!source) return;
    let live = true;
    setError(null);
    const hit = cachedDiagram(source);
    if (hit !== null) {
      setSvg(hit);
      return;
    }
    setSvg(null);
    void renderDiagram(source)
      .then((clean) => {
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
  }, [source]);

  if (!source) return null;
  if (error) {
    return <FigureCard kind="mermaid" svg={null} source={source} error="Mermaid 图表无法渲染" detail={error} />;
  }
  if (!svg) return <FigurePlaceholder label="正在渲染图表" />;
  return <FigureCard kind="mermaid" svg={svg} source={source} />;
}
