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

/**
 * markdown 围栏上的语言名 → Shiki 语法。
 *
 * 围栏里写的是 ```js / ```py / ```sh 这类习惯叫法，跟文件后缀不是一套表（c++、console、
 * golang 这些两边都没有），所以单独记一张。键一律小写：围栏语言名大小写混用得很常见。
 */
const fenceAliases: Record<string, CodeLanguage> = {
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  node: "javascript",
  ts: "typescript",
  tsx: "typescript",
  mts: "typescript",
  cts: "typescript",
  py: "python",
  python: "python",
  sh: "shellscript",
  bash: "shellscript",
  zsh: "shellscript",
  shell: "shellscript",
  console: "shellscript",
  shellscript: "shellscript",
  yml: "yaml",
  yaml: "yaml",
  rb: "ruby",
  ruby: "ruby",
  rs: "rust",
  rust: "rust",
  go: "go",
  golang: "go",
  c: "c",
  h: "c",
  "c++": "cpp",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  cs: "csharp",
  "c#": "csharp",
  csharp: "csharp",
  kt: "kotlin",
  kts: "kotlin",
  kotlin: "kotlin",
  swift: "swift",
  java: "java",
  php: "php",
  lua: "lua",
  sql: "sql",
  json: "json",
  jsonc: "json",
  toml: "toml",
  ini: "ini",
  cfg: "ini",
  conf: "ini",
  md: "markdown",
  markdown: "markdown",
  mdx: "mdx",
  html: "html",
  htm: "html",
  xml: "xml",
  css: "css",
  scss: "scss",
  less: "less",
};

/**
 * 围栏语言名 → 语法。认不出来返回 undefined，调用方按纯文本渲染。
 * 先按 canonical 名试（languageExtensions 的键），再查别名表。
 */
export function languageForFence(name: string): CodeLanguage | undefined {
  const key = name.trim().toLowerCase();
  if (isCodeLanguage(key)) return key;
  return Object.prototype.hasOwnProperty.call(fenceAliases, key) ? fenceAliases[key] : undefined;
}
