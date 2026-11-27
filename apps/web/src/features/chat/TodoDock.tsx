import { ChevronDown, ChevronUp, Circle, CircleCheck, ListTodo, Loader2 } from "lucide-react";
import { memo, useMemo, useState } from "react";

import type { ChatMessage, ChatToolCall } from "@/store/chat";
import { useChatStore } from "@/store/chat";

type TodoStatus = "pending" | "in_progress" | "completed";
type TodoItem = { content: string; status: TodoStatus };

const STATUS_LABEL: Record<TodoStatus, string> = {
  completed: "已完成",
  in_progress: "进行中",
  pending: "待处理",
};

/** 一次 todo_write 的入参就是完整列表；形状不对（老会话、模型写错字段）就当没有这一份。 */
function readTodos(tool: ChatToolCall): TodoItem[] | null {
  const raw = tool.input?.todos;
  if (!Array.isArray(raw)) return null;
  const items: TodoItem[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const row = entry as { content?: unknown; status?: unknown };
    if (typeof row.content !== "string" || !row.content.trim()) continue;
    const status: TodoStatus =
      row.status === "completed" || row.status === "in_progress" ? row.status : "pending";
    items.push({ content: row.content.trim(), status });
  }
  return items.length ? items : null;
}

/**
 * 会话里最新一份任务清单。
 *
 * 工具每次传的都是完整列表（替换语义），所以倒着找最后一次没出错的调用就是当前清单。
 * 倒着找还顺带解决了一个时序问题：调用一落地入参就有了，列表不必等工具结果回贴。
 */
function latestTodos(messages: ChatMessage[]): TodoItem[] {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const steps = messages[i].steps;
    if (!steps?.length) continue;
    for (let j = steps.length - 1; j >= 0; j -= 1) {
      const tools = steps[j].tools;
      if (!tools?.length) continue;
      for (let k = tools.length - 1; k >= 0; k -= 1) {
        const tool = tools[k];
        if (tool.name !== "todo_write" || tool.isError) continue;
        const items = readTodos(tool);
        if (items) return items;
      }
    }
  }
  return [];
}

/** 头部摘要：各状态计数用 · 连起来，计数为 0 的一段不写（空段是噪声）。 */
function progressLabel(todos: TodoItem[]): string {
  const done = todos.filter((item) => item.status === "completed").length;
  const active = todos.filter((item) => item.status === "in_progress").length;
  const pending = todos.length - done - active;
  return [
    ...(done > 0 ? [`${done} 项完成`] : []),
    ...(active > 0 ? [`${active} 项进行中`] : []),
    ...(pending > 0 ? [`${pending} 项待处理`] : []),
  ].join(" · ");
}

function StatusGlyph({ status }: { status: TodoStatus }) {
  if (status === "completed") return <CircleCheck className="size-4 text-faint" />;
  if (status === "in_progress") return <Loader2 className="size-4 animate-spin text-accent" />;
  return <Circle className="size-4 text-faint" />;
}

/**
 * 对话底部的任务清单坞。
 *
 * 清单本身来自工具调用（todo_write 的入参），所以不用后端再开一条通道：
 * 工具每次传全量、UI 只认最后一次，刷新历史会话时同样能还原。
 * 收起时只留标题和进度摘要（对白区底部一行的高度），点开才铺列表。
 */
export const TodoDock = memo(function TodoDock() {
  const messages = useChatStore((state) => state.messages);
  const todos = useMemo(() => latestTodos(messages), [messages]);
  const [collapsed, setCollapsed] = useState(true);
  if (todos.length === 0) return null;

  return (
    <div className="shrink-0 px-6 pb-2">
      <section
        aria-label="待办"
        data-todo-dock="true"
        className="mx-auto max-w-[52rem] rounded-xl border border-line bg-card px-3 py-1.5 shadow-[0_6px_18px_var(--shadow-color-soft)]"
      >
        <button
          type="button"
          title="更新任务清单"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((value) => !value)}
          className="flex w-full min-w-0 cursor-pointer items-center gap-2.5 text-left"
        >
          <span className="grid size-4 shrink-0 place-items-center text-faint">
            <ListTodo className="size-4" />
          </span>
          <span className="shrink-0 text-[13px] font-medium leading-6 text-ink">待办</span>
          <span className="min-w-0 flex-auto truncate text-[13px] leading-5 text-faint">
            {progressLabel(todos)}
          </span>
          <span className="grid size-4 shrink-0 place-items-center text-faint">
            {collapsed ? <ChevronDown className="size-4" /> : <ChevronUp className="size-4" />}
          </span>
        </button>
        {collapsed ? null : (
          <ul className="mt-1.5 flex max-h-[180px] flex-col gap-2 overflow-y-auto">
            {todos.map((item, index) => (
              <li
                key={`${index}-${item.content}`}
                className="flex min-w-0 items-center gap-2.5 text-[13px] leading-5 text-muted"
              >
                <span
                  className="grid size-4 shrink-0 place-items-center"
                  role="img"
                  aria-label={STATUS_LABEL[item.status]}
                >
                  <StatusGlyph status={item.status} />
                </span>
                <span className="min-w-0 truncate" title={item.content}>
                  {item.content}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
});
