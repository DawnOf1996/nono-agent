import { createdBundledHighlighter, type HighlighterGeneric, type ThemedToken } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

import { isCodeLanguage, type CodeLanguage } from "@/features/files/preview/languages";

const theme = "github-light" as const;

const loaders = {
  typescript: () => import("@shikijs/langs/typescript"),
  javascript: () => import("@shikijs/langs/javascript"),
  shellscript: () => import("@shikijs/langs/shellscript"),
  json: () => import("@shikijs/langs/json"),
  python: () => import("@shikijs/langs/python"),
  ruby: () => import("@shikijs/langs/ruby"),
  go: () => import("@shikijs/langs/go"),
  rust: () => import("@shikijs/langs/rust"),
  java: () => import("@shikijs/langs/java"),
  c: () => import("@shikijs/langs/c"),
  cpp: () => import("@shikijs/langs/cpp"),
  csharp: () => import("@shikijs/langs/csharp"),
  kotlin: () => import("@shikijs/langs/kotlin"),
  swift: () => import("@shikijs/langs/swift"),
  php: () => import("@shikijs/langs/php"),
  yaml: () => import("@shikijs/langs/yaml"),
  toml: () => import("@shikijs/langs/toml"),
  ini: () => import("@shikijs/langs/ini"),
  markdown: () => import("@shikijs/langs/markdown"),
  mdx: () => import("@shikijs/langs/mdx"),
  html: () => import("@shikijs/langs/html"),
  css: () => import("@shikijs/langs/css"),
  scss: () => import("@shikijs/langs/scss"),
  less: () => import("@shikijs/langs/less"),
  sql: () => import("@shikijs/langs/sql"),
  xml: () => import("@shikijs/langs/xml"),
  lua: () => import("@shikijs/langs/lua"),
} satisfies Record<CodeLanguage, () => Promise<{ default: unknown }>>;

const createPreviewHighlighter = createdBundledHighlighter({
  langs: loaders,
  themes: {
    "github-light": () => import("@shikijs/themes/github-light"),
  },
  engine: () => createJavaScriptRegexEngine(),
});

let pending: Promise<HighlighterGeneric<CodeLanguage, typeof theme>> | null = null;

function highlighter(): Promise<HighlighterGeneric<CodeLanguage, typeof theme>> {
  pending ??= createPreviewHighlighter({
    themes: [theme],
    langs: ["typescript", "javascript", "json"],
  });
  return pending;
}

/** 把源码分成带颜色的行。不认识的语法返回 null，调用方显示纯文本。 */
export async function highlightCode(code: string, language: string): Promise<ThemedToken[][] | null> {
  if (!isCodeLanguage(language)) return null;
  const engine = await highlighter();
  if (!engine.getLoadedLanguages().includes(language)) await engine.loadLanguage(language);
  return engine.codeToTokens(code, { lang: language, theme }).tokens;
}
