import { workspaceFileRawUrl } from "@/api/client";
import { normalizeWorkspace, parentPath } from "@/lib/workspace";

const REMOTE_URL = /^[a-z][a-z0-9+.-]*:/i;

function pathKey(path: string): string {
  return normalizeWorkspace(path).toLowerCase();
}

/** 折叠 `.` / `..`，结果使用反斜杠，方便和 Windows 工作区路径比较。 */
function normalizeDots(path: string): string {
  const slash = path.replace(/\\/g, "/");
  const drive = /^([a-zA-Z]:)/.exec(slash)?.[1];
  const body = drive ? slash.slice(drive.length) : slash;
  const out: string[] = [];
  for (const part of body.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      out.pop();
      continue;
    }
    out.push(part);
  }
  return drive ? `${drive}\\${out.join("\\")}` : out.join("\\");
}

/**
 * 把 Markdown / HTML 里的图片地址改写成工作区媒体路由。
 * 远程地址和锚点保持原样；越出工作区的本地路径不加载。
 */
export function previewImageSrc(root: string, filePath: string, src: string | undefined): string | undefined {
  if (!src) return undefined;
  const dest = src.trim();
  if (!dest || dest.startsWith("#") || dest.startsWith("data:")) return dest;
  if (REMOTE_URL.test(dest) && !/^[a-zA-Z]:[\\/]/.test(dest)) return dest;
  const absolute =
    /^[a-zA-Z]:[\\/]/.test(dest) || dest.startsWith("/") || dest.startsWith("\\")
      ? dest
      : `${parentPath(filePath)}/${dest}`;
  const normalized = normalizeDots(absolute);
  const rootKey = pathKey(root);
  const fullKey = pathKey(normalized);
  if (fullKey !== rootKey && !fullKey.startsWith(`${rootKey}\\`)) return undefined;
  return workspaceFileRawUrl(root, normalizeWorkspace(normalized));
}
