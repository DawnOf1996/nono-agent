/** 把已清洗的 SVG 画成 PNG。根节点常是百分比宽度，先按 viewBox 钉成像素再栅格化。 */

function pixelSize(root: Element): { width: number; height: number } {
  const viewBox = root.getAttribute("viewBox");
  if (viewBox) {
    const parts = viewBox.trim().split(/[\s,]+/).map(Number);
    if (parts.length === 4 && parts[2] > 0 && parts[3] > 0) {
      return { width: parts[2], height: parts[3] };
    }
  }
  const width = Number.parseFloat(root.getAttribute("width") ?? "");
  const height = Number.parseFloat(root.getAttribute("height") ?? "");
  if (width > 0 && height > 0) return { width, height };
  return { width: 960, height: 540 };
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("无法导出图片"));
    image.src = url;
  });
}

export async function svgToPngBlob(markup: string): Promise<Blob> {
  const doc = new DOMParser().parseFromString(markup, "image/svg+xml");
  if (doc.querySelector("parsererror") !== null) throw new Error("无法导出图片");
  const root = doc.documentElement;
  if (root.localName.toLowerCase() !== "svg") throw new Error("无法导出图片");
  const size = pixelSize(root);
  const maxEdge = 4096;
  const scale = Math.min(2, maxEdge / Math.max(size.width, size.height));
  const width = Math.max(1, Math.round(size.width * scale));
  const height = Math.max(1, Math.round(size.height * scale));
  root.setAttribute("width", String(size.width));
  root.setAttribute("height", String(size.height));
  if (!root.getAttribute("xmlns")) root.setAttribute("xmlns", "http://www.w3.org/2000/svg");

  const blob = new Blob([new XMLSerializer().serializeToString(root)], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const image = await loadImage(url);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("无法导出图片");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(image, 0, 0, width, height);
    return await new Promise((resolve, reject) => {
      canvas.toBlob((png) => (png ? resolve(png) : reject(new Error("无法导出图片"))), "image/png");
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
