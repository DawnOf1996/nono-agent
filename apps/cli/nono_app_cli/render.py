"""把 SDK 消息流渲染成终端输出。"""

from __future__ import annotations

from typing import Any

from nono_agent.core.engine import QueryEngine
from nono_agent.core.types import (
    SDKAssistantMessage,
    SDKResultMessage,
    SDKStreamEventMessage,
    SDKSystemMessage,
)
from nono_agent.observability.token_tracker import (
    context_usage_color,
    format_context_usage,
)


def format_token_summary(usage: Any) -> str:
    """把 Usage 渲染成一致的 token 摘要（四类 + 总计）。

    与 ``/tokens`` 命令（``TokensCommand``）口径一致：完整列出 input / output /
    cache_read / cache_creation 与总计，避免只显示三项导致数值与实际总用量对不上。
    """
    return (
        f"in={usage.input_tokens} out={usage.output_tokens} "
        f"cache_read={usage.cache_read_input_tokens} "
        f"cache_creation={usage.cache_creation_input_tokens} "
        f"(total {usage.total_tokens})"
    )


def render_text(content: Any) -> str:
    """从消息的 content 字段提取可渲染文本 —— 处理各种内容块类型。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    parts.append(f"[Tool: {block.get('name', '?')}]")
                elif block_type == "tool_result":
                    result_text = block.get("content", "")
                    if isinstance(result_text, str) and len(result_text) > 200:
                        result_text = result_text[:200] + "..."
                    parts.append(f"[Result: {result_text}]")
                elif block_type == "thinking":
                    thinking_text = block.get("thinking", "")
                    if thinking_text:
                        parts.append(f"── Thinking ──\n{thinking_text}\n── End Thinking ──")
        return "\n".join(parts)
    return str(content)


def apply_delta(block: dict[str, Any], delta: dict[str, Any]) -> None:
    """将流式 delta 合并到内容块中。"""
    delta_type = delta.get("type", "")
    if delta_type == "text_delta":
        block["text"] = block.get("text", "") + delta.get("text", "")
    elif delta_type == "input_json_delta":
        current = block.get("input", "")
        if isinstance(current, dict):
            current = ""
        block["input"] = current + delta.get("partial_json", "")
    elif delta_type == "thinking_delta":
        block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")


def render_blocks(blocks: list[dict[str, Any]]) -> str:
    """从内容块列表渲染可显示的增量文本。

    注意：流式路径不包含 thinking 的视觉标记（由事件处理器控制），
    避免增量计算时标记残留在输出中间。
    """
    parts = []
    for block in blocks:
        block_type = block.get("type", "")
        if block_type == "text":
            parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            name = block.get("name", "?")
            parts.append(f"[Tool: {name}]")
        elif block_type == "thinking":
            thinking = block.get("thinking", "")
            if thinking:
                parts.append(thinking)
    return "\n".join(parts)


async def consume_stream(
    engine: QueryEngine,
    user_input: str,
    console: Any,
) -> SDKResultMessage | None:
    """消费 submit_message 流并渲染输出。

    流式渲染策略：
    - 通过 SDKStreamEventMessage 接收 content_block_delta 事件
    - 对每个 text_delta 实时追加打印（打字机效果）
    - 流完成后通过 SDKAssistantMessage 做最终渲染（回退方案）
    """
    is_streaming = False
    last_text = ""
    current_blocks: list[dict[str, Any]] = []
    has_thinking = False

    async for sdk_msg in engine.submit_message(user_input):
        if isinstance(sdk_msg, SDKStreamEventMessage):
            event = sdk_msg.event
            event_type = event.get("type", "")

            if event_type == "content_block_start":
                block = event.get("content_block", {})
                if isinstance(block, dict):
                    current_blocks.append({**block})
                    block_type = block.get("type", "")
                    if block_type == "thinking":
                        has_thinking = True
                        if not is_streaming:
                            is_streaming = True
                            console.print("\n[bold magenta]Assistant:[/bold magenta]")
                        console.print("[dim italic]── Start Thinking ──[/dim italic]")
                    elif block_type == "text" and has_thinking:
                        console.print()

            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                idx = event.get("index", -1)
                if isinstance(delta, dict) and 0 <= idx < len(current_blocks):
                    apply_delta(current_blocks[idx], delta)

                new_text = render_blocks(current_blocks)
                if new_text and new_text != last_text:
                    if not is_streaming:
                        is_streaming = True
                        console.print("\n[bold magenta]Assistant:[/bold magenta]")
                    incremental = new_text[len(last_text) :]
                    if incremental:
                        console.print(incremental, end="", highlight=False)
                    last_text = new_text

            elif event_type == "content_block_stop":
                block_idx = event.get("index", -1)
                if 0 <= block_idx < len(current_blocks):
                    if current_blocks[block_idx].get("type") == "thinking":
                        console.print("\n[dim]── End Thinking ──[/dim]")
                        last_text = render_blocks(current_blocks)

        elif isinstance(sdk_msg, SDKAssistantMessage):
            text = render_text(sdk_msg.message.get("content", ""))
            if text:
                if not is_streaming:
                    console.print(f"\n[bold magenta]Assistant:[/bold magenta]\n{text}")
                else:
                    console.print()

        elif isinstance(sdk_msg, SDKSystemMessage):
            subtype = sdk_msg.subtype
            if subtype == "api_retry":
                console.print("[yellow][!] Retrying...[/yellow]")
            elif subtype == "tool_result":
                content = getattr(sdk_msg, "content", None) or ""
                if content:
                    console.print(f"[dim]  {content}[/dim]")
            elif subtype == "loop_terminal":
                pass
            elif subtype == "request_start":
                current_blocks = []
                last_text = ""
                is_streaming = False
                has_thinking = False
                console.print("[dim]~ Waiting...[/dim]")

        elif isinstance(sdk_msg, SDKResultMessage):
            token_str = format_token_summary(sdk_msg.usage)
            if sdk_msg.is_error:
                console.print(
                    f"\n[red]Error: {sdk_msg.subtype}[/red]\n"
                    f"Duration: {sdk_msg.duration_ms:.0f}ms | "
                    f"Turns: {sdk_msg.num_turns} | "
                    f"Tokens: {token_str}"
                )
            else:
                console.print(
                    f"\n[dim][OK] Completed in {sdk_msg.duration_ms:.0f}ms | "
                    f"{sdk_msg.num_turns} turns | "
                    f"{token_str}[/dim]"
                )
            ctx_usage = sdk_msg.context_usage
            ctx_line = format_context_usage(ctx_usage)
            if ctx_line:
                color = context_usage_color(float(ctx_usage.get("usage_ratio", 0.0)))
                console.print(f"[{color}]{ctx_line}[/{color}]")
            return sdk_msg

    return None
