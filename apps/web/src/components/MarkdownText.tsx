import type { Components } from "react-markdown";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
};

export function MarkdownText({
  text,
  tone = "body",
}: {
  text: string;
  tone?: "body" | "muted";
}) {
  return (
    <div className={tone === "muted" ? "prose-md prose-md-muted" : "prose-md"}>
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </Markdown>
    </div>
  );
}
