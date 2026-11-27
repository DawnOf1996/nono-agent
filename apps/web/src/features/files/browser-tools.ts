import { encodeImages, type PendingImage } from "@/lib/images";

/**
 * 内部浏览器的取内容能力。像素只在主进程手里，所以下面每个"拿东西"的函数
 * 都是「主进程截一张 → 渲染进程裁剪 → 交给对话」的形状。
 *
 * 纯 web 版（vite dev server 直接打开）没有 `nonoDesktop` 桥，只有 iframe，
 * 而跨域 iframe 的像素对父页面是污染的 —— canvas.toDataURL 会抛安全错误。
 * 所以这个文件里的东西一律只在桌面版下调用，调用点自己判 `desktop`。
 */

/**
 * `<webview>` 上按需用到的成员。
 *
 * 它不是普通 DOM 元素，也不参与 React 协调，所以只能按用到的成员局部声明、由 DOM
 * 直接挂载。这里是唯一的声明处 —— BrowserPane 也从这里取，省得两处各写一份、
 * 改一个方法漏掉另一个。
 */
export type WebviewElement = HTMLDivElement & {
  /** 导航要用它：webview 挂载后给 src 赋值不会开始导航，页面会停在 about:blank。 */
  loadURL(url: string): Promise<void>;
  reload(): void;
  canGoBack(): boolean;
  canGoForward(): boolean;
  goBack(): void;
  goForward(): void;
  getURL(): string;
  getTitle(): string;
  getWebContentsId(): number;
  /** 在访客页主世界求值。sandbox=yes 也不影响 —— 是宿主主动调用，跟页面有没有 Node 无关。 */
  executeJavaScript(code: string): Promise<unknown>;
};

export function desktopBridge() {
  return typeof window === "undefined" ? undefined : window.nonoDesktop;
}

/** 截当前可见区域，回 base64 PNG。页面还没就绪 / guest 已销毁都会落到 error 分支。 */
export async function captureViewport(tag: WebviewElement): Promise<{ data?: string; error?: string }> {
  const bridge = desktopBridge();
  if (!bridge?.captureWebview) return { error: "截图只能在桌面版里用" };
  let id: number;
  try {
    id = tag.getWebContentsId();
  } catch {
    // guest 进程没起来时 getWebContentsId 会抛，页面多半也还没加载。
    return { error: "页面还没加载好" };
  }
  try {
    const result = await bridge.captureWebview(id);
    if (!result.ok || !result.data) return { error: result.error || "截图失败" };
    return { data: result.data };
  } catch (error) {
    return { error: error instanceof Error ? error.message : "截图失败" };
  }
}

/**
 * base64 PNG → 对话框里的图片附件。
 *
 * 特意绕一圈走 `encodeImages`（而不是自己拼 PendingImage）：体积上限、
 * 长边 2048 的缩放、超限转 JPEG 都在那边，改一处就够，截图和粘贴走同一套规格。
 */
export async function pendingImageFromPng(name: string, base64: string): Promise<PendingImage> {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const file = new File([bytes], `${name}.png`, { type: "image/png" });
  const [image] = await encodeImages([file]);
  return image;
}

export type CaptureRect = { x: number; y: number; width: number; height: number };

/**
 * 把选区夹进视口内。
 *
 * 元素可以比视口还高、也可以有负坐标（滚出屏幕外了），直接拿去裁会算出负的宽高。
 * 夹一次比在裁剪函数里到处防边界好。
 */
export function clampRect(rect: CaptureRect, width: number, height: number): CaptureRect {
  const x = Math.min(Math.max(0, rect.x), Math.max(0, width));
  const y = Math.min(Math.max(0, rect.y), Math.max(0, height));
  return {
    x,
    y,
    width: Math.max(0, Math.min(rect.width, width - x)),
    height: Math.max(0, Math.min(rect.height, height - y)),
  };
}

function decodePng(base64: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("截图解码失败"));
    img.src = `data:image/png;base64,${base64}`;
  });
}

function canvasToBase64(canvas: HTMLCanvasElement): Promise<string> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("截图裁剪失败"));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const result = typeof reader.result === "string" ? reader.result : "";
        // dataUrl 形如 data:image/png;base64,XXXX，取逗号后面那段。
        resolve(result.slice(result.indexOf(",") + 1));
      };
      reader.onerror = () => reject(new Error("截图裁剪失败"));
      reader.readAsDataURL(blob);
    }, "image/png");
  });
}

/**
 * 从整张视口截图里裁出选区。
 *
 * `capturePage()` 回的是 guest 视口的位图，位图像素和访客页的 CSS 像素不是一回事
 * （高 DPI 屏上差一个 devicePixelRatio）。这里不猜比例，直接拿「位图宽度 ÷ webview
 * 元素的 CSS 宽度」算出来 —— 这样缩放因子变了也不会裁歪。
 */
export async function cropCapture(
  base64: string,
  viewportCssWidth: number,
  rect: CaptureRect,
): Promise<string> {
  const img = await decodePng(base64);
  const ratio = viewportCssWidth > 0 ? img.naturalWidth / viewportCssWidth : 1;
  const sx = Math.max(0, Math.round(rect.x * ratio));
  const sy = Math.max(0, Math.round(rect.y * ratio));
  const sw = Math.max(1, Math.min(Math.round(rect.width * ratio), img.naturalWidth - sx));
  const sh = Math.max(1, Math.min(Math.round(rect.height * ratio), img.naturalHeight - sy));
  const canvas = document.createElement("canvas");
  canvas.width = sw;
  canvas.height = sh;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("截图裁剪失败");
  ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
  return canvasToBase64(canvas);
}

/** mousemove 时只问"这是个什么、在哪儿"，够画高亮框了。 */
export type HoverProbe = {
  tag: string;
  x: number;
  y: number;
  width: number;
  height: number;
  label: string;
};

/** 点定之后才问全的：选择器、属性、HTML、正文。 */
export type PickedElement = HoverProbe & {
  selector: string;
  html: string;
  text: string;
  attrs: Record<string, string>;
  pageUrl: string;
  pageTitle: string;
};

function probeScript(x: number, y: number): string {
  return `(() => {
    const el = document.elementFromPoint(${x}, ${y});
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      x: r.x, y: r.y, width: r.width, height: r.height,
      label: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
    };
  })()`;
}

/**
 * 选择器生成：从元素往上走到 html，有 id 就在 id 那里停。同标签兄弟超过一个才补
 * :nth-of-type，否则路径会长得没法看。
 */
function selectorScript(): string {
  return `(() => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift(part + '#' + cur.id); break; }
      const cls = (cur.getAttribute('class') || '').trim().split(/\\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) part += '.' + cls.join('.');
      const parent = cur.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter((s) => s.tagName === cur.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent;
    }
    parts.unshift('html');
    return parts.join(' > ');
  })()`;
}

function fullScript(x: number, y: number): string {
  return `(() => {
    const el = document.elementFromPoint(${x}, ${y});
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const attrs = {};
    for (const a of Array.from(el.attributes || [])) {
      if (a.name === 'style' || a.name.length > 60) continue;
      attrs[a.name] = a.value.length > 200 ? a.value.slice(0, 200) + '…' : a.value;
    }
    return {
      tag: el.tagName.toLowerCase(),
      x: r.x, y: r.y, width: r.width, height: r.height,
      label: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
      selector: ${selectorScript()},
      html: (el.outerHTML || '').slice(0, 4000),
      text: (el.innerText || '').trim().slice(0, 600),
      attrs,
      pageUrl: location.href,
      pageTitle: document.title,
    };
  })()`;
}

async function runScript<T>(tag: WebviewElement, code: string): Promise<T | null> {
  try {
    return ((await tag.executeJavaScript(code)) as T | null) ?? null;
  } catch {
    // 页面正在导航、或脚本上下文刚被销毁。这里只是"这一帧没问到"，不该中断整个交互。
    return null;
  }
}

export function probeElement(tag: WebviewElement, x: number, y: number): Promise<HoverProbe | null> {
  return runScript<HoverProbe>(tag, probeScript(x, y));
}

export function pickElement(tag: WebviewElement, x: number, y: number): Promise<PickedElement | null> {
  return runScript<PickedElement>(tag, fullScript(x, y));
}

/**
 * 元素描述 → 引用的正文。
 *
 * 模型读不到那个访客页（它在另一个浏览上下文里），只能靠这段文字定位，所以选择器和
 * outerHTML 是主体，位置和属性是佐证。
 */
export function describeElement(el: PickedElement): string {
  const lines = [
    `页面：${el.pageTitle || "(无标题)"}`,
    el.pageUrl,
    "",
    `元素：<${el.tag}>${el.text ? ` ${el.text.slice(0, 120)}` : ""}</${el.tag}>`,
    `选择器：${el.selector}`,
    `位置：${Math.round(el.x)}, ${Math.round(el.y)}　${Math.round(el.width)}×${Math.round(el.height)}`,
  ];
  const attrs = Object.entries(el.attrs);
  if (attrs.length) {
    lines.push(`属性：${attrs.map(([key, value]) => `${key}="${value}"`).join("　")}`);
  }
  if (el.html) {
    lines.push("", "HTML：", el.html);
  }
  return lines.join("\n");
}
