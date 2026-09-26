/** 与 Shiki 语法包对应的后缀。新增一种代码预览时，这里和 highlight.ts 的加载器要一起加。 */
const languageExtensions = {
  typescript: ["ts", "tsx", "mts", "cts"],
  javascript: ["js", "jsx", "mjs", "cjs"],
  shellscript: ["sh", "bash", "zsh"],
  json: ["json", "jsonc", "jsonl", "ndjson"],
  python: ["py", "pyw", "pyi"],
  ruby: ["rb", "rake", "gemspec"],
  go: ["go"],
  rust: ["rs"],
  java: ["java"],
  c: ["c", "h"],
  cpp: ["cc", "cpp", "cxx", "hh", "hpp", "hxx"],
  csharp: ["cs"],
  kotlin: ["kt", "kts"],
  swift: ["swift"],
  php: ["php"],
  yaml: ["yaml", "yml"],
  toml: ["toml"],
  ini: ["ini"],
  markdown: ["md", "markdown"],
  mdx: ["mdx"],
  html: ["html", "htm", "xhtml"],
  css: ["css"],
  scss: ["scss"],
  less: ["less"],
  sql: ["sql"],
  xml: ["xml", "xsd", "xsl", "xslt"],
  lua: ["lua"],
} as const;

export type CodeLanguage = keyof typeof languageExtensions;

export const CODE_EXTENSIONS: readonly string[] = Object.values(languageExtensions).flat();

const byExtension = new Map<string, CodeLanguage>();
for (const [language, extensions] of Object.entries(languageExtensions) as [CodeLanguage, readonly string[]][]) {
  for (const extension of extensions) byExtension.set(extension, language);
}

export function languageForPath(filePath: string): CodeLanguage | undefined {
  const extension = /\.([^./]+)$/u.exec(filePath.replaceAll("\\", "/"))?.[1]?.toLowerCase();
  return extension === undefined ? undefined : byExtension.get(extension);
}

export function isCodeLanguage(value: string): value is CodeLanguage {
  return Object.prototype.hasOwnProperty.call(languageExtensions, value);
}
