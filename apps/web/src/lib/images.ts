import type { ChatImageAttachment, UploadedAsset } from "@nono/protocol";

export const MAX_PENDING_IMAGES = 8;
export const ACCEPT_IMAGE = "image/png,image/jpeg,image/webp,image/gif,image/bmp";

const MAX_ORIGINAL_BYTES = 20 * 1024 * 1024;
const MAX_EDGE = 2048;
const KEEP_ORIGINAL_BYTES = 1.5 * 1024 * 1024;

export type PendingImage = {
  id: string;
  name: string;
  mediaType: string;
  data: string;
  preview: string;
};

/**
 * 把待发图片先传到服务端的资产目录，再换成资产引用。
 *
 * 走这条路的收益：base64 不再进 `/chat` 的 JSON，也不再进会话事件流 ——
 * 会话文件里只留一个引用，图片字节留在磁盘上。单个上传失败时退回
 * base64 形态（旧路径，服务端仍然支持），不因为一次上传失败丢掉整条消息。
 */
export async function uploadPendingImages(images: PendingImage[]): Promise<ChatImageAttachment[]> {
  const out: ChatImageAttachment[] = [];
  for (const item of images) {
    const uploaded = await uploadAsset(item).catch(() => null);
    if (!uploaded) {
      out.push(toBase64Attachment(item));
      continue;
    }
    out.push({
      type: "image",
      source: {
        type: "asset",
        asset_id: uploaded.asset_id,
        media_type: uploaded.media_type,
        path: uploaded.relative_path,
        sha256: uploaded.sha256,
        bytes: uploaded.byte_size,
        width: uploaded.width,
        height: uploaded.height,
      },
    });
  }
  return out;
}

/** 旧路径：图片字节直接带在请求里。服务端仍然支持，作为上传失败时的兜底。 */
function toBase64Attachment(item: PendingImage): ChatImageAttachment {
  return {
    type: "image",
    source: { type: "base64", media_type: item.mediaType, data: item.data },
  };
}

async function uploadAsset(item: PendingImage): Promise<UploadedAsset> {
  const form = new FormData();
  form.append("file", base64ToBlob(item.data, item.mediaType), item.name || "image.png");
  // 路径是 /asset（单数）—— /assets 归打包后的前端产物（JS/CSS chunk）用。
  const response = await fetch("/asset", { method: "POST", body: form });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return (await response.json()) as UploadedAsset;
}

function base64ToBlob(data: string, mediaType: string): Blob {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mediaType });
}

export function filesFromClipboard(event: { clipboardData?: DataTransfer | null }): File[] {
  const files: File[] = [];
  const items = event.clipboardData?.items;
  if (!items) return files;
  for (const item of items) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) files.push(file);
    }
  }
  return files;
}

export function filesFromList(list: FileList | File[] | null | undefined): File[] {
  if (!list) return [];
  return Array.from(list).filter((file) => file.type.startsWith("image/"));
}

export async function encodeImages(files: File[]): Promise<PendingImage[]> {
  const out: PendingImage[] = [];
  for (const file of files) {
    out.push(await encodeImage(file));
  }
  return out;
}

async function encodeImage(file: File): Promise<PendingImage> {
  if (!file.type.startsWith("image/")) {
    throw new Error("只能添加图片");
  }
  if (file.size > MAX_ORIGINAL_BYTES) {
    throw new Error("图片不能超过 20MB");
  }
  const keepOriginal =
    file.type === "image/gif" ||
    file.type === "image/svg+xml" ||
    file.size <= KEEP_ORIGINAL_BYTES;
  if (keepOriginal) {
    return fromDataUrl(file.name || "image.png", await readAsDataUrl(file), file.type);
  }

  const bitmap = await createImageBitmap(file);
  const { width, height } = scaleToMax(bitmap.width, bitmap.height, MAX_EDGE);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    bitmap.close();
    throw new Error("无法处理图片");
  }
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  const blob = await canvasToBlob(canvas, "image/jpeg", 0.86);
  const name = file.name.replace(/\.[^.]+$/, "") || "image";
  return fromDataUrl(`${name}.jpg`, await readAsDataUrl(blob), "image/jpeg");
}

function scaleToMax(width: number, height: number, maxEdge: number): { width: number; height: number } {
  const edge = Math.max(width, height);
  if (edge <= maxEdge) return { width, height };
  const ratio = maxEdge / edge;
  return {
    width: Math.max(1, Math.round(width * ratio)),
    height: Math.max(1, Math.round(height * ratio)),
  };
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("图片压缩失败"));
    }, type, quality);
  });
}

function readAsDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") resolve(reader.result);
      else reject(new Error("无法读取图片"));
    };
    reader.onerror = () => reject(new Error("无法读取图片"));
    reader.readAsDataURL(file);
  });
}

function fromDataUrl(name: string, dataUrl: string, fallbackType: string): PendingImage {
  const match = /^data:([^;]+);base64,(.+)$/.exec(dataUrl);
  const mediaType = match?.[1] || fallbackType || "image/png";
  const data = match?.[2] || "";
  if (!data) throw new Error("无法读取图片");
  return {
    id: crypto.randomUUID(),
    name,
    mediaType,
    data,
    preview: dataUrl,
  };
}
