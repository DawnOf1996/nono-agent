"""命令输出的字节解码 —— 必须在**原始字节还在**的地方完成。

编码声明（:mod:`nono_agent.shell.preamble`）只钉死 PowerShell 自身产生的输出；
子进程的 stdout 编码仍由子进程决定，中文 Windows 上就是 CP936/GBK。所以解码
必须落在字节层：一旦先用 utf-8 + ``replace`` 解过一轮，原始字节就被 U+FFFD
覆盖，之后再怎么「回退」也只是把替换字符重新编码一遍，拿不回真字符。

回退链：utf-8-sig → utf-8 → 本机默认编码 → gb18030 → utf-8 + ``replace``。
候选与 :func:`nono_agent.tools.builtin._common.decode_text` 同一套，但只服务命令
输出（文件文本有各自的判定需求，不共用这条）。
"""

from __future__ import annotations

import locale

_ASCII_FAMILY = frozenset({"ascii", "us-ascii", "ansi-x3.4-1968"})


def _candidates() -> list[str]:
    """UTF-8 strict → 本机默认编码 → gb18030。去重、跳过 ASCII。"""
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        key = name.lower().replace("_", "-")
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


def _strict(data: bytes) -> str | None:
    """按候选链 strict 解码。全部失败返回 None。"""
    for encoding in _candidates():
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def decode_output(data: bytes | bytearray) -> str:
    """解码一段命令输出。

    顺序有意为之：

    1. 整段 utf-8 strict；
    2. **仅当**失败点在末尾时，退到「截掉末尾不完整字符」的前半段再试 utf-8。
       只在 utf-8 分支内做这个补偿 —— 让其它候选去认领半截字符，gb18030 会把
       残字节配成一个**看似合法的错字**，比留个替换符更难发现；
    3. 本机默认编码 / gb18030 整段 strict；
    4. 都解不开 → utf-8 + ``replace``。宁可给模型替换符，也不要整段静默丢弃。
    """
    raw = bytes(data)
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.end == len(raw):
            head = raw[: exc.start]
            try:
                return head.decode("utf-8")
            except UnicodeDecodeError:
                pass
    text = _strict(raw)
    return raw.decode("utf-8", errors="replace") if text is None else text


__all__ = ["decode_output"]
