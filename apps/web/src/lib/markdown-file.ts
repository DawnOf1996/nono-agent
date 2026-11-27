const INVALID_NAME = /[<>:"/\\|?*\u0000-\u001f]/;

/** 从回答里取标题或首行，作为默认的 .md 文件名。 */
export function suggestMarkdownFileName(text: string): string {
  const heading = /^#{1,6}[ \t]+(.+)$/m.exec(text)?.[1] ?? "";
  const firstLine = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
  const raw = (heading || firstLine || "导出").replace(/[#*`[\]]/g, " ");
  const cleaned = raw
    .replace(INVALID_NAME, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[。！？!?.,，、；;：:\s]+$/g, "");
  const base = (cleaned || "导出").slice(0, 80);
  return base.toLowerCase().endsWith(".md") ? base : `${base}.md`;
}
