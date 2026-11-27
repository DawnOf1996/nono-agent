import { FileText, Folder } from "lucide-react";
import { memo, useMemo } from "react";

import { usePanelStore } from "@/features/files/panel-store";
import { mentionBaseName, parseMentions } from "@/lib/mentions";
import { joinWorkspace } from "@/lib/workspace";

/**
 * 对话区里的 @引用。
 *
 * 引用在正文里只是一段普通文字（模型看到的就是这段文字），但在界面上要认出来：
 * 高亮成一枚标签，点一下就在侧边文件树里打开它。
 */

/**
 * 引用高亮的底色。输入框和对话区用的是同一套颜色，只是形状不同：
 * 输入框里不能加内边距（那会让高亮层和输入框的文字对不齐），靠一圈外扩的
 * 阴影补出那点空隙。
 */
export const MENTION_CLASS = "rounded-[3px] bg-info-soft text-info";
export const MENTION_SHADOW = "0 0 0 2px var(--color-info-soft)";

/** 对话区里的引用标签。点一下就在侧边文件树里打开。 */
export const MentionChip = memo(function MentionChip({
  path,
  isDir,
  root,
}: {
  path: string;
  isDir: boolean;
  root: string;
}) {
  const revealFile = usePanelStore((state) => state.revealFile);
  const absolute = useMemo(() => joinWorkspace(root, path), [root, path]);
  return (
    <button
      type="button"
      className="mx-px inline-flex max-w-full items-center gap-1 rounded bg-info-soft px-1.5 py-px align-baseline text-info hover:opacity-80"
      title={absolute}
      onClick={(event) => {
        event.stopPropagation();
        revealFile(absolute, isDir);
      }}
    >
      {isDir ? <Folder className="size-3 shrink-0" /> : <FileText className="size-3 shrink-0" />}
      <span className="min-w-0 truncate">{mentionBaseName(path)}</span>
    </button>
  );
});

/**
 * 把正文里的 @引用 换成可点的标签，其余文字原样保留。
 *
 * 引用之外的文字仍然是纯文本 —— 用户消息不走 Markdown（终端输出按原样显示才对）。
 */
export function MentionText({ text, root }: { text: string; root: string }) {
  const segments = useMemo(() => parseMentions(text), [text]);
  const hasMention = segments.some((segment) => segment.kind === "mention");
  if (!hasMention) return <>{text}</>;
  return (
    <>
      {segments.map((segment, index) =>
        segment.kind === "text" ? (
          <span key={index}>{segment.text}</span>
        ) : (
          <MentionChip key={index} path={segment.path} isDir={segment.isDir} root={root} />
        ),
      )}
    </>
  );
}
