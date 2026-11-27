"""服务端终端模拟器：屏幕状态的唯一真源。

会话的字节流先进 ``TerminalScreen``，由它维护一份 VT 状态；客户端重连时
``snapshot()`` 序列化出一段**语法完整**的 ANSI 流，客户端 xterm ``reset()`` +
``write()`` 之后屏幕与 Host 端逐格一致。

这样做换来的东西：转义序列永不被截断、尺寸变化后历史会重排、可以按行而不是
按字符界定保留量。代价是 Host 每个会话常驻一个模拟器，且它成为唯一真源——
任何改变屏幕的操作（喂输出、resize、生成快照）都必须串行落在事件循环线程上。

序列化刻意只覆盖**可见视口**，与 ``@xterm/headless`` + ``SerializeAddon`` 的
无参 ``serialize()`` 同义：重连后客户端的滚动历史从空开始，视口精确恢复。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pyte
from pyte.screens import Char

# 256 色索引 -> 基础 16 色的终端名，用于把 pyte 的具名色映射回 SGR 参数。
_NAMED: Final[frozenset[str]] = frozenset(
    {
        "black", "red", "green", "brown", "blue", "magenta", "cyan", "white",
        "brightblack", "brightred", "brightgreen", "brightbrown", "brightblue",
        "brightmagenta", "brightcyan", "brightwhite",
    }
)

# 具名前景色 -> SGR 30-37 / 90-97。
_FG: Final[dict[str, int]] = {
    "black": 30, "red": 31, "green": 32, "brown": 33,
    "blue": 34, "magenta": 35, "cyan": 36, "white": 37,
    "brightblack": 90, "brightred": 91, "brightgreen": 92, "brightbrown": 93,
    "brightblue": 94, "brightmagenta": 95, "brightcyan": 96, "brightwhite": 97,
}

# 具名背景色 -> SGR 40-47 / 100-107。
_BG: Final[dict[str, int]] = {
    "black": 40, "red": 41, "green": 42, "brown": 43,
    "blue": 44, "magenta": 45, "cyan": 46, "white": 47,
    "brightblack": 100, "brightred": 101, "brightgreen": 102, "brightbrown": 103,
    "brightblue": 104, "brightmagenta": 105, "brightcyan": 106, "brightwhite": 107,
}

# ``Screen.set_mode(private=True)`` 把参数左移 5 位存放，1049h 落在 1049 << 5。
_ALTERNATE: Final[int] = 1049 << 5

_DEFAULT: Final[Char] = Char(
    data=" ", fg="default", bg="default", bold=False, italics=False,
    underscore=False, strikethrough=False, reverse=False, blink=False,
)

# 这些属性组合对应 pyte ``Char`` 上的布尔字段，SGR 参数按此顺序拼接。
_ATTRS: Final[tuple[tuple[str, int], ...]] = (
    ("bold", 1),
    ("italics", 3),
    ("underscore", 4),
    ("blink", 5),
    ("reverse", 7),
    ("strikethrough", 9),
)


@dataclass(frozen=True, slots=True)
class _Style:
    """一个单元格的绘制属性；相等性用于跳过大段无变化的 SGR。"""

    fg: str
    bg: str
    bold: bool
    italics: bool
    underscore: bool
    strikethrough: bool
    reverse: bool
    blink: bool

    @classmethod
    def of(cls, char: Char) -> _Style:
        return cls(
            char.fg, char.bg, char.bold, char.italics, char.underscore,
            char.strikethrough, char.reverse, char.blink,
        )

    def sgr(self) -> str:
        """生成把终端属性设为本样式的完整 SGR 序列。"""
        parts: list[int | str] = [0]
        for name, code in _ATTRS:
            if getattr(self, name):
                parts.append(code)
        if self.fg != "default":
            parts.append(_FG[self.fg] if self.fg in _FG else _rgb(self.fg, 38))
        if self.bg != "default":
            parts.append(_BG[self.bg] if self.bg in _BG else _rgb(self.bg, 48))
        return f"\x1b[{';'.join(str(part) for part in parts)}m"


def _rgb(value: str, base: int) -> str:
    """把 pyte 的 6 位十六进制颜色展开成真彩色 SGR 参数。

    pyte 把 256 色和真彩色统一规范化成 ``rrggbb`` 字符串，所以两者共用
    ``38/48;2;r;g;b``；终端会把落在 256 色立方体内的值自行降级。
    """
    if len(value) != 6:
        return str(base)
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return f"{base};2;{red};{green};{blue}"


class TerminalScreen:
    """一个会话的服务端模拟器。全部方法只在事件循环线程调用。"""

    def __init__(self, cols: int, rows: int, history: int) -> None:
        self._screen = pyte.HistoryScreen(cols, rows, history=max(0, history))
        self._stream = pyte.ByteStream(self._screen)

    @property
    def cols(self) -> int:
        return self._screen.columns

    @property
    def rows(self) -> int:
        return self._screen.lines

    def feed(self, data: bytes) -> None:
        """把 PTY 原始字节喂进模拟器。

        走字节而不是先 decode：``ByteStream`` 自带增量 UTF-8 解码器，跨读取
        边界的多字节序列不会被截成 U+FFFD。
        """
        if data:
            self._stream.feed(data)

    def resize(self, cols: int, rows: int) -> None:
        """按 PTY 先、模拟器后的顺序重排（调用方负责保证这个顺序）。"""
        if cols != self._screen.columns or rows != self._screen.lines:
            self._screen.resize(rows, cols)

    def display(self) -> list[str]:
        """可见视口的纯文本行，供诊断与测试使用。"""
        return list(self._screen.display)

    def snapshot(self) -> str:
        """序列化可见视口为一段合法 ANSI 流。

        客户端应当先 ``reset()`` 再 ``write()`` 本串：这里从 ``ESC[H`` 起
        逐行重画，因此重连后不留上一任连接的残影。
        """
        parts: list[str] = ["\x1b[0m"]
        if _ALTERNATE in self._screen.mode:
            # 备用屏（Vim、less）里视口是独立缓冲，不切过去就会重画到主屏上。
            # 只进不出：退出备用屏的 ``?1049l`` 会随之后的字节流到达，快照若自己
            # 补一发就等于把刚重画的视口换回主屏丢掉。
            parts.append("\x1b[?1049h")
        parts.append("\x1b[H\x1b[2J")
        for y in range(self._screen.lines):
            parts.append(f"\x1b[{y + 1};1H")
            parts.append(self._row(y))
        cursor = self._screen.cursor
        # pyte 用 x == columns 表示"待换行"（差一个字符就要折行），而 CUP 没有
        # 对应的可表达状态，钳到末列。差异只影响紧接着的下一个字符，客户端收到
        # 后续字节流时会自行重建该状态，不值得为它让快照带上多余字符。
        column = min(cursor.x + 1, self._screen.columns)
        parts.append(f"\x1b[{cursor.y + 1};{column}H")
        parts.append("\x1b[?25h" if not cursor.hidden else "\x1b[?25l")
        parts.append("\x1b[0m")
        return "".join(parts)

    def _row(self, y: int) -> str:
        """重画一行；末尾与默认样式一致的空白不发出，压缩快照体积。"""
        cells = self._screen.buffer[y]
        out: list[str] = []
        style: _Style | None = None
        for x in range(self._screen.columns):
            char = cells[x]
            if char.data == "":
                # 宽字符的占位续格：光标已由前一格推进两列，这里必须跳过，
                # 否则快照宽度会随宽字符漂移。
                continue
            current = _Style.of(char)
            if current != style:
                out.append(current.sgr())
                style = current
            out.append(char.data)
        return "".join(out)


__all__ = ["TerminalScreen"]
