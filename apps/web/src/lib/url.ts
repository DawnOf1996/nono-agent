/**
 * 只放行 http/https 的地址归一化，供地址栏、Markdown 外链、浏览器新标签共用。
 * 没有协议时补 https；非 http(s) 与解析失败一律返回 null，调用方按“拒绝”处理。
 */
export function httpUrl(raw: string): string | null {
  const text = raw.trim();
  if (!text) return null;
  const withScheme = /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`;
  try {
    const url = new URL(withScheme);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.toString();
  } catch {
    return null;
  }
}