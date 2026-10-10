import { ChevronDown, ChevronUp } from "lucide-react";
import { memo, useCallback, useLayoutEffect, useRef, useState } from "react";

/**
 * 用户消息的正文气泡。
 *
 * 正文按纯文本渲染，不走 Markdown：这类内容绝大多数是终端输出、报错栈和命令，
 * 里面的 `~`、`_`、`*`、行首 `#`、管道符在 GFM 里全是语法标记 —— 一段
 * `kubectl exec ... -- \` 里两个波浪号会被 micromark 配成删除线（strikethrough 的
    10| * singleTilde 默认开），折行又被 CommonMark 当 softbreak 吃掉，于是原文里的换行
 * 消失、还凭空多出一道横线。终端输出按原样显示才是对的，等宽 + pre-wrap 一视同仁。
 */

/** 折叠阈值：正文超过这么多次行高就收起，只留开头一段。 */
const COLLAPSE_LINES = 10;

/** 收起时末行往下淡出的高度，够盖住一行半，不至于把字削成半截。 */
const FADE_PX = 36;

/** 视口封顶：矮窗口里别让一条消息吃掉小半个屏幕。 */
const MAX_VIEWPORT_RATIO = 0.4;

/** 两套排版：多行按终端输出走等宽（对得上列），单行是人在说话，走正文更好看。 */
const MONO_BODY = "font-mono text-[13px] leading-6";
const PLAIN_BODY = "text-[14px] leading-[22px]";

const MONO_LINE_HEIGHT = 24;
const PLAIN_LINE_HEIGHT = 22;

/**
 * 收起时保留的高度。
 *
 * 以行高为单位算，两套排版各自十行 —— 写死一个 px 的话，等宽正文会比正文版多露一截，
 * 两者的折叠线就不在同一个位置了。再按视口封顶：窗口矮的时候，十行可能就顶掉小半屏。
 */
function collapseHeight(lineHeight: number): number {
  const lines = COLLAPSE_LINES * lineHeight;
  if (typeof window === "undefined") return lines;
  return Math.min(lines, Math.round(window.innerHeight * MAX_VIEWPORT_RATIO));
}

/**
 * 多行正文按终端输出排版。
 *
 * 只看行数，不去猜终端特征：日志和报错几乎总是多行，而人顺手敲的多半是一两句话，
 * 这个区分够用，也不会把「先看下这个 ↓ 下面三行」这种带换行的短句误判成日志。
 */
function looksLikeTerminal(text: string): boolean {
  return text.split("\n").length > 1;
}

/**
 * 记下正文有没有超出折叠高度。
 *
 * max-height 是常驻的，判定直接读 scrollHeight / clientHeight —— 比数行数可靠：
 * 长路径、宽表格在等宽下会自己折行，按行数估会算错。展开时不再重算，免得底栏
 * 因为「不溢出」而消失、收起就找不回来了。
 */
function useOverflowing(bodyRef: React.RefObject<HTMLElement | null>, expanded: boolean): boolean {
  const [overflowing, setOverflowing] = useState(false);
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;

  const measure = useCallback(() => {
    const node = bodyRef.current;
    if (!node || expandedRef.current) return;
    setOverflowing(node.scrollHeight - node.clientHeight > 1);
  }, [bodyRef]);

  // useLayoutEffect 在绘制前同步重渲染：首帧虽然还没算出是否溢出，底栏就已经补上，
  // 用户看不到「内容被裁掉但无处可展开」的中间态。观察自身而不是依赖宽度，
  // 侧栏拖宽拖窄、字号变化都会重新判定。
  useLayoutEffect(() => {
    const node = bodyRef.current;
    if (!node) return;
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [measure, bodyRef]);

  return overflowing;
}

export const UserBubble = memo(function UserBubble({ text }: { text: string }) {
  const terminal = looksLikeTerminal(text);
  const [expanded, setExpanded] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const overflowing = useOverflowing(bodyRef, expanded);
  const collapsedHeight = collapseHeight(terminal ? MONO_LINE_HEIGHT : PLAIN_LINE_HEIGHT);
  // 渐隐只在真的裁掉了东西时画。收起状态下无条件铺这层 mask 的话，一行消息也要中招：
  // 正文行高 22px，比 FADE_PX（36px）还矮，渐变的起点算出来是负数，整行字都会被
  // 洗成半透明 —— 看上去像发灰，其实下面什么都没有可展开。
  const faded = !expanded && overflowing;

  return (
    <div className="max-w-full overflow-hidden rounded-2xl bg-card">
      <div className={`px-4 pt-2.5 ${overflowing ? "pb-1" : "pb-2.5"}`}>
        {/* 渐隐画在正文自己身上（mask），不是在下面垫一条渐变块：后者会在正文和遮罩之间
            留一道台阶，看着像「下面还粘着一小段别的内容」。 */}
        <div
          ref={bodyRef}
          style={{
            maxHeight: expanded ? "none" : collapsedHeight,
            ...(faded
              ? {
                  WebkitMaskImage: `linear-gradient(to bottom, #000 calc(100% - ${FADE_PX}px), transparent)`,
                  maskImage: `linear-gradient(to bottom, #000 calc(100% - ${FADE_PX}px), transparent)`,
                }
              : {}),
          }}
          className={`overflow-hidden break-words whitespace-pre-wrap text-ink ${
            terminal ? MONO_BODY : PLAIN_BODY
          }`}
        >
          {text}
        </div>
      </div>
      {overflowing ? (
        // 不画分隔线：底栏只是正文的一个出口，不是气泡的第二块板。只靠颜色分主次，
        // 整条气泡还是一整块底色。
        <button
          type="button"
          aria-expanded={expanded}
          title={expanded ? "收起" : "查看全部"}
          onClick={() => setExpanded((value) => !value)}
          className="flex w-full cursor-pointer items-center gap-1 px-4 pb-1.5 pt-0.5 text-left text-[12px] leading-5 text-faint hover:text-ink"
        >
          {expanded ? <ChevronUp className="size-3.5 shrink-0" /> : <ChevronDown className="size-3.5 shrink-0" />}
          {expanded ? "收起" : "查看全部"}
        </button>
      ) : null}
    </div>
  );
});