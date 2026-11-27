import type { DocumentPreviewDefinition } from "@/features/files/preview/types";

/** 纯文本兜底。宿主总会把它附在候选列表末尾。 */
export const PLAIN_PREVIEW_ID = "@nono/preview/text";

const listeners = new Set<() => void>();
let registered: DocumentPreviewDefinition[] = [];
let snapshot: readonly DocumentPreviewDefinition[] = registered;

function publish(): void {
  snapshot = registered.slice();
  for (const listener of listeners) listener();
}

/**
 * 按后缀列出可用预览，extension 优先于 builtin，再比更长的后缀和注册顺序。
 */
export function matchingDocumentPreviews(
  definitions: readonly DocumentPreviewDefinition[],
  path: string,
): readonly DocumentPreviewDefinition[] {
  const normalized = path.replaceAll("\\", "/").toLowerCase();
  const name = normalized.slice(normalized.lastIndexOf("/") + 1);
  return definitions
    .map((definition, order) => ({
      definition,
      order,
      rank: definition.priority === "builtin" ? 0 : 1,
      length: Math.max(
        0,
        ...definition.extensions
          .map((extension) => extension.toLowerCase().replace(/^\./u, ""))
          .filter((extension) => extension.length > 0 && name.endsWith(`.${extension}`))
          .map((extension) => extension.length),
      ),
    }))
    .filter((candidate) => candidate.length > 0)
    .sort((left, right) => right.rank - left.rank || right.length - left.length || left.order - right.order)
    .map((candidate) => candidate.definition);
}

/** 匹配到的实现，再加上纯文本兜底。 */
export function previewCandidates(
  definitions: readonly DocumentPreviewDefinition[],
  path: string,
): readonly DocumentPreviewDefinition[] {
  const matched = matchingDocumentPreviews(definitions, path).filter((item) => item.id !== PLAIN_PREVIEW_ID);
  const fallback = definitions.find((item) => item.id === PLAIN_PREVIEW_ID);
  return fallback === undefined ? matched : [...matched, fallback];
}

export function getDocumentPreviews(): readonly DocumentPreviewDefinition[] {
  return snapshot;
}

export function subscribeDocumentPreviews(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * 注册一种文件预览。重复的 id 会抛错。返回的函数会卸下这次注册。
 */
export function registerDocumentPreview(definition: DocumentPreviewDefinition): () => void {
  if (registered.some((item) => item.id === definition.id)) {
    throw new Error(`document preview already registered: ${definition.id}`);
  }
  registered = [...registered, definition];
  publish();
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    registered = registered.filter((item) => item.id !== definition.id);
    publish();
  };
}
