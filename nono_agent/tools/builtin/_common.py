"""内置工具共享辅助函数 —— 路径解析、输出截断、错误包装、文本解码。"""

from __future__ import annotations

import difflib
import locale
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.shell.encoding import decode_output

# 所有 strict 候选均失败时的占位编码名（utf-8 + replace）
LOSSY_ENCODING = "utf-8-replace"

# UI 附件（文件差异）的大小上限。这份数据不进模型上下文，但要进 SSE 和会话日志，
# 所以仍然要封顶：4 KB 的差异最多几百行，够卡片显示，超了就只给统计。
_UI_DIFF_MAX_LINES = 200
_UI_DIFF_MAX_CHARS = 8000
# 取"写之前正文"的体积上限：更大的文件不取基线（不为了画卡片去读半个 GB）。
_UI_BASELINE_MAX_BYTES = 1_000_000


def file_diff_ui(
    path: str,
    before: str | None,
    after: str,
) -> dict[str, Any]:
    """整文件前后差异 —— 给 UI 的附件（ToolResult.ui），不进模型上下文。

    行号必须是**整文件**的：对话里的 diff 卡片要显示"第几行被改了"，
    拿工具入参（old_string/new_string）自己算只能得到片段内的相对编号，
    和文件对不上。所以这里对写前/写后的完整正文做 unified diff，
    @@ 头里的起止行号就是文件里的真实行号。

    Args:
        path: 目标文件路径（原样回给前端，不做归一）。
        before: 写之前的正文；None 表示文件原先不存在（新建）。
        after: 写之后的正文。

    Returns:
        ``{"kind": "file_diff", ...}``。差异超限时 diff 为 None、truncated 为 True，
        前端据此退化成"只给新内容"，不拿增删配色糊弄。
    """
    before_lines = [] if before is None else before.splitlines()
    after_lines = after.splitlines()
    diff = list(
        difflib.unified_diff(
            before_lines, after_lines, fromfile="a", tofile="b", lineterm="", n=3
        )
    )
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    payload: dict[str, Any] = {
        "kind": "file_diff",
        "path": path,
        "created": before is None,
        "added": added,
        "removed": removed,
        "diff": None,
        "truncated": True,
    }
    if 0 < len(diff) <= _UI_DIFF_MAX_LINES:
        text = "\n".join(diff)
        if len(text) <= _UI_DIFF_MAX_CHARS:
            payload["diff"] = text
            payload["truncated"] = False
    return payload


def read_baseline(path: str) -> str | None:
    """读一个文件当前正文作为差异基线。

    读不到（不存在/二进制/太大/非 UTF-8 系）返回 None，调用方据此按"没有基线"处理。
    """
    try:
        with open(path, "rb") as f:
            data = f.read(_UI_BASELINE_MAX_BYTES + 1)
    except OSError:
        return None
    if len(data) > _UI_BASELINE_MAX_BYTES or is_binary(data):
        return None
    text, encoding = decode_text(data)
    return None if encoding == LOSSY_ENCODING else text


_UTF8_FAMILY = frozenset({"utf-8", "utf8", "utf-8-sig"})
_ASCII_FAMILY = frozenset({"ascii", "us-ascii", "ansi-x3.4-1968"})


def resolve_path(context: Any, path: str) -> str:
    """通过上下文解析路径 —— 优先使用路径沙箱。"""
    return str(context.resolve_path(path))


def error_result(tool_name: str, message: str) -> ToolResult:
    """构造 is_error=True 的工具结果。"""
    return ToolResult(
        tool_use_id="",
        content=f"Error: {message}",
        is_error=True,
        tool_name=tool_name,
    )


def ok_result(tool_name: str, content: str) -> ToolResult:
    """构造成功的工具结果。"""
    return ToolResult(
        tool_use_id="",
        content=content,
        tool_name=tool_name,
    )


def truncate_text(text: str, max_chars: int = 30_000) -> str:
    """截断过长文本 —— 保留头部与尾部。"""
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return head + f"\n... [truncated, {len(text)} chars total] ...\n" + tail


def is_binary(data: bytes) -> bool:
    """启发式判断字节内容是否为二进制（含 NUL 字节）。"""
    return b"\x00" in data[:8192]


def _normalize_encoding_name(name: str) -> str:
    return name.lower().replace("_", "-")


def is_utf8_family(encoding: str) -> bool:
    """是否为 UTF-8 系（含 BOM）。"""
    return _normalize_encoding_name(encoding) in _UTF8_FAMILY


def encoding_note(encoding: str) -> str | None:
    """非 UTF-8 解码时给模型看的一行说明；UTF-8 返回 None。"""
    if is_utf8_family(encoding):
        return None
    if encoding == LOSSY_ENCODING:
        return (
            "[decoded as utf-8 with replacement characters; source encoding is uncertain]"
        )
    return f"[decoded as {encoding}]"


def _decode_candidates() -> list[str]:
    """UTF-8 strict → 本机默认编码 → gb18030。去重、跳过 ASCII。"""
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        key = _normalize_encoding_name(name)
        if not name or key in seen or key in _ASCII_FAMILY:
            return
        seen.add(key)
        names.append(name)

    add("utf-8-sig")
    add("utf-8")
    try:
        preferred = locale.getpreferredencoding(False) or ""
    except (OSError, ValueError):
        preferred = ""
    add(preferred)
    add("gb18030")
    return names


def decode_command_output(data: bytes | bytearray) -> str:
    """解码命令的实时输出。

    实现已下沉到 :func:`nono_agent.shell.encoding.decode_output` —— 候选链与
    边界处理在那边是单一出处，bash 工具的输出与本函数走的是同一条路径。
    """
    return decode_output(data)


def decode_text(data: bytes) -> tuple[str, str]:
    """把字节解码为文本。

    先 utf-8-sig / utf-8（strict），失败再试本机默认编码与 gb18030；
    仍失败则 utf-8 ``errors=replace``，编码名记为 ``utf-8-replace``。
    """
    if not data:
        return "", "utf-8"
    for encoding in _decode_candidates():
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), LOSSY_ENCODING
