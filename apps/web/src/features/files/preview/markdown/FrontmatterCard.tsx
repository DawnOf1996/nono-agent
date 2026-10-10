import { ChevronRight } from "lucide-react";
import { memo, useMemo, useState } from "react";

import { CodeFence } from "@/components/CodeFence";
import {
  frontmatterSummary,
  parseFrontmatterFields,
  type FrontmatterField,
} from "@/features/files/preview/markdown/frontmatter";

/** 超过这个字段数就默认收起：元信息不该把正文挤到首屏之外。 */
const COLLAPSE_OVER = 8;

/**
 * 不用 h1–h6：MdToc 靠 querySelectorAll("h1, ..., h6") 收大纲，卡片里出个标题
 * 就会在目录里多一条"元信息"。语义上这里本来就是字段表，用 dl。
 */
function FieldValue({ field }: { field: FrontmatterField }) {
  if (field.list) {
    if (!field.list.length) return <span className="md-frontmatter-empty">空列表</span>;
    return (
      <span className="md-frontmatter-chips">
        {field.list.map((item, index) => (
          <code key={`${item}-${index}`} className="md-frontmatter-chip">
            {item}
          </code>
        ))}
      </span>
    );
  }
  if (field.raw !== undefined) {
    // 没硬解出来的字段：原样贴着，不猜它是什么意思。
    return <pre className="md-frontmatter-raw">{field.raw}</pre>;
  }
  if (!field.scalar) return <span className="md-frontmatter-empty">（空）</span>;
  return <span className="md-frontmatter-value">{field.scalar}</span>;
}

function RawYaml({ raw }: { raw: string }) {
  // 只传 <code>，别自己再套 <pre>：CodeFence 从 children[0] 的 className 上取语言名，
  // 而 markdown 里 pre 的组件拿到的 children 本来就是 <code>。多包一层它就只看到
  // 一个没有 class 的 <pre>，语言名退回 text，语法高亮也跟着没了。
  return (
    <CodeFence>
      <code className="language-yaml">{raw}</code>
    </CodeFence>
  );
}

export type FrontmatterCardProps = {
  /** frontmatter 原文（不含首尾 `---`）。 */
  raw: string;
  /** compact 是聊天里的一行摘要，card 是预览里的元信息卡片。 */
  variant: "card" | "compact";
};

/**
 * frontmatter 的呈现层。抽取在 frontmatter.ts，这里只管怎么显示。
 *
 * 任何形态下都能切到原文 —— 解析只覆盖顶层标量和列表，其余一律原样展示，
 * 不给"卡片比文件真实内容更完整"的错觉。
 */
export const FrontmatterCard = memo(function FrontmatterCard({ raw, variant }: FrontmatterCardProps) {
  const fields = useMemo(() => parseFrontmatterFields(raw), [raw]);
  const [open, setOpen] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const compact = variant === "compact";
  const summary = useMemo(() => frontmatterSummary(fields), [fields]);
  const expanded = compact ? open : open || fields.length <= COLLAPSE_OVER;

  if (raw.trim() === "") {
    // 有头没内容：只在预览里留个极轻的标记，聊天里直接不占地方。
    if (compact) return null;
    return (
      <div className="md-frontmatter md-frontmatter-empty-block" data-md-frontmatter="">
        <span>frontmatter（空）</span>
      </div>
    );
  }

  return (
    <div className={compact ? "md-frontmatter md-frontmatter-compact" : "md-frontmatter"} data-md-frontmatter="">
      <div className="md-frontmatter-bar">
        <button
          type="button"
          className="md-frontmatter-toggle"
          aria-expanded={expanded}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronRight className={`md-frontmatter-caret${expanded ? " md-frontmatter-caret-open" : ""}`} />
          <span className="md-frontmatter-label">frontmatter</span>
          <span className="md-frontmatter-count">{fields.length} 个字段</span>
        </button>
        {compact && summary ? <span className="md-frontmatter-summary">{summary}</span> : null}
        {expanded ? (
          <button
            type="button"
            className="md-frontmatter-source"
            title={showRaw ? "按字段显示" : "看原始 YAML"}
            onClick={() => setShowRaw((value) => !value)}
          >
            {showRaw ? "看字段" : "看原文"}
          </button>
        ) : null}
      </div>
      {expanded ? (
        showRaw ? (
          <RawYaml raw={raw} />
        ) : (
          <dl className="md-frontmatter-fields">
            {fields.map((field, index) => (
              <div key={`${field.key}-${index}`} className="md-frontmatter-row">
                <dt>{field.key}</dt>
                <dd>
                  <FieldValue field={field} />
                </dd>
              </div>
            ))}
          </dl>
        )
      ) : null}
    </div>
  );
});