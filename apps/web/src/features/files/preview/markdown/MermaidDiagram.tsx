import { Children, isValidElement, useEffect, useState, type ReactNode } from "react";

import { FigureCard, FigurePlaceholder } from "@/components/FigureCard";
import { sanitizeMermaidSvg } from "@/features/files/preview/markdown/svg";
import { useResolvedTheme, type ResolvedTheme } from "@/lib/theme";

let mermaidReady: Promise<typeof import("mermaid").default> | null = null;
let configured: ResolvedTheme | null = null;

async function loadMermaid(theme: ResolvedTheme) {
  mermaidReady ??= import("mermaid").then((module) => module.default);
  const mermaid = await mermaidReady;
  // mermaid 的主题是全局配置、不是每次调用的参数，所以切外观必须重新 initialize。
  // 深色用它自带的 dark 主题（自带一套深底配色），浅色保持原来的 neutral。
  if (configured !== theme) {
    configured = theme;
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      htmlLabels: false,
      theme: theme === "dark" ? "dark" : "neutral",
    });
  }
  return mermaid;
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

function cachedDiagram(cacheKey: string): string | null {
  return renderedCache.get(cacheKey) ?? null;
}

function renderDiagram(cacheKey: string, source: string, theme: ResolvedTheme): Promise<string> {
  const hit = inFlight.get(cacheKey);
  if (hit) return hit;
  const task = loadMermaid(theme)
    .then(async (mermaid) => {
      renderSeq += 1;
      const rendered = await renderOffscreen(mermaid, `mermaid-${renderSeq}`, source);
      const clean = sanitizeMermaidSvg(rendered.svg);
      if (!clean) throw new Error("图表未通过安全检查");
      if (renderedCache.size >= CACHE_LIMIT) {
        const oldest = renderedCache.keys().next();
        if (!oldest.done) renderedCache.delete(oldest.value);
      }
      renderedCache.set(cacheKey, clean);
      return clean;
    })
    .finally(() => inFlight.delete(cacheKey));
  inFlight.set(cacheKey, task);
  return task;
}

/** 缓存必须把主题算进 key：同一段源码在深浅两套下是两张不同的图，共用一份就会串色。 */
function cacheKeyOf(source: string, theme: ResolvedTheme): string {
  return `${theme}${source}`;
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
  const theme = useResolvedTheme();
  const cacheKey = cacheKeyOf(source, theme);
  // 首帧就命中缓存的话，连「正在渲染图表」都不会闪一下。
  const [svg, setSvg] = useState<string | null>(() => cachedDiagram(cacheKey));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!source) return;
    let live = true;
    setError(null);
    const hit = cachedDiagram(cacheKey);
    if (hit !== null) {
      setSvg(hit);
      return;
    }
    setSvg(null);
    void renderDiagram(cacheKey, source, theme)
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
  }, [source, cacheKey, theme]);

  if (!source) return null;
  if (error) {
    return <FigureCard kind="mermaid" svg={null} source={source} error="Mermaid 图表无法渲染" detail={error} />;
  }
  if (!svg) return <FigurePlaceholder label="正在渲染图表" />;
  return <FigureCard kind="mermaid" svg={svg} source={source} />;
}
