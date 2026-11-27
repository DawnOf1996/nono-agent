const MAX_ASSET_BYTES = 4 * 1024 * 1024;
const MAX_TOTAL_BYTES = 32 * 1024 * 1024;
const MAX_ASSETS = 64;

export type HtmlAssetKind = "script" | "stylesheet";

export type HtmlAsset = {
  kind: HtmlAssetKind;
  reference: string;
  text: string;
};

export type HtmlBundle = {
  html: string;
  assets: HtmlAsset[];
};

export type ReadHtmlRelative = (reference: string, signal: AbortSignal) => Promise<Uint8Array>;

function decodeUtf8(data: Uint8Array): string {
  return new TextDecoder("utf-8", { fatal: true }).decode(data);
}

/** 只接受相对当前文件的路径，不接受协议、绝对路径和空引用。 */
export function isRelativeAsset(reference: string): boolean {
  return reference.length > 0 && !/^(?:[a-z][a-z\d+.-]*:|[/\\#?])/iu.test(reference) && !reference.includes("\0");
}

/**
 * 把 HTML 文件所在目录和相对引用拼成交给宿主的路径。
 * 越过盘符或文件系统根时返回 null，由服务端再做工作区边界检查。
 */
export function resolvePreviewRelative(filePath: string, reference: string): string | null {
  const cut = reference.search(/[?#]/u);
  const raw = (cut === -1 ? reference : reference.slice(0, cut)).trim();
  if (!isRelativeAsset(raw)) return null;
  const normalized = filePath.replaceAll("\\", "/");
  const slash = normalized.lastIndexOf("/");
  const segments = (slash === -1 ? "" : normalized.slice(0, slash)).split("/");
  for (const part of raw.replaceAll("\\", "/").split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (segments.length === 0 || segments[segments.length - 1] === "") return null;
      segments.pop();
      continue;
    }
    segments.push(part);
  }
  const joined = segments.join("/");
  return joined.length > 0 ? joined : null;
}

/**
 * 收集页面里直接引用的经典脚本和样式表。
 * 有 `<base href>`、模块脚本、CSS url/@import 时不跟过去。
 */
export async function packHtml(data: Uint8Array, readRelative: ReadHtmlRelative, signal: AbortSignal): Promise<HtmlBundle> {
  signal.throwIfAborted();
  let total = data.byteLength;
  if (total > MAX_TOTAL_BYTES) throw new Error("HTML 依赖超过大小上限");
  const html = decodeUtf8(data);
  const template = document.createElement("template");
  template.innerHTML = html;
  if (template.content.querySelector("base[href]") !== null) return { html, assets: [] };

  const assets: HtmlAsset[] = [];
  const seen = new Set<string>();
  for (const element of template.content.querySelectorAll("script[src],link[href]")) {
    const script = element.localName === "script";
    const type = element.getAttribute("type")?.trim().toLowerCase() ?? "";
    if (script && !["", "text/javascript", "application/javascript"].includes(type)) continue;
    if (!script && !(element.getAttribute("rel") ?? "").toLowerCase().split(/\s+/u).includes("stylesheet")) continue;
    const reference = element.getAttribute(script ? "src" : "href");
    if (reference === null) continue;
    const suffix = reference.search(/[?#]/u);
    const assetPath = suffix === -1 ? reference : reference.slice(0, suffix);
    if (!isRelativeAsset(reference) || !(script ? /\.js$/iu : /\.css$/iu).test(assetPath)) continue;
    const kind: HtmlAssetKind = script ? "script" : "stylesheet";
    const key = `${kind}:${reference}`;
    if (seen.has(key)) continue;
    if (assets.length >= MAX_ASSETS) throw new Error("HTML 依赖太多");
    signal.throwIfAborted();
    const bytes = await readRelative(reference, signal);
    signal.throwIfAborted();
    if (bytes.byteLength > MAX_ASSET_BYTES) throw new Error("HTML 依赖超过大小上限");
    total += bytes.byteLength;
    if (total > MAX_TOTAL_BYTES) throw new Error("HTML 依赖超过大小上限");
    assets.push({ kind, reference, text: decodeUtf8(bytes) });
    seen.add(key);
  }
  return { html, assets };
}
