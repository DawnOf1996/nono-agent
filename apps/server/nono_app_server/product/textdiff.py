"""文本对比：把两份内容或 git 的 unified diff 转成前端统一使用的文件对比结构。

文件对比（FileDiff）的形状：

```
{
  "kind": "text" | "binary" | "oversized",
  "path": str, "old_path": str | None,
  "status": "added" | "modified" | "deleted" | "renamed",
  "before": bool, "after": bool,
  "hunks": [{"old_start", "old_lines", "new_start", "new_lines", "header",
             "lines": ["+x", "-y", " z"]}],
  "added": int, "deleted": int,
  "coarse": bool,
}
```
"""

from __future__ import annotations

import difflib
from typing import Any

HUNK_CONTEXT = 3
BINARY_SNIFF_BYTES = 8000
#: 任一侧超过这么多行时不做逐行对比，整段标为替换，避免 difflib 在超大文件上卡住。
MAX_DIFF_LINES = 20_000


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:BINARY_SNIFF_BYTES]


def decode_text(data: bytes) -> str | None:
    """按 UTF-8（含 BOM）→ GB18030 顺序解码；像二进制就返回 None。"""
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("gb18030")
    except UnicodeDecodeError:
        return None
    sample = text[:4000]
    if not sample:
        return text
    weird = 0
    for char in sample:
        code = ord(char)
        if char in "\n\r\t":
            continue
        if code < 32 or code == 0xFFFD:
            weird += 1
    if weird / len(sample) >= 0.02:
        return None
    return text


def decode_for_diff(data: bytes) -> str | None:
    """同 decode_text，但保留开头的 BOM（\\ufeff），只增删 BOM 的改动在对比里也看得见。"""
    if data.startswith(b"\xef\xbb\xbf") and b"\0" not in data:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return decode_text(data)


def split_lines(text: str) -> list[str]:
    """按行切分，去掉行尾换行符；末尾换行不产生空行。"""
    return text.splitlines()


def _status(before: bool, after: bool) -> str:
    if not before:
        return "added"
    if not after:
        return "deleted"
    return "modified"


def diff_texts(
    old: str, new: str, context: int = HUNK_CONTEXT
) -> tuple[list[dict[str, Any]], int, int, bool]:
    """两段文本的 unified hunk。返回 (hunks, added, deleted, coarse)。"""
    a = split_lines(old)
    b = split_lines(new)
    if len(a) > MAX_DIFF_LINES or len(b) > MAX_DIFF_LINES:
        if a == b:
            return [], 0, 0, False
        hunk = {
            "old_start": 1 if a else 0,
            "old_lines": len(a),
            "new_start": 1 if b else 0,
            "new_lines": len(b),
            "header": "",
            "lines": [f"-{line}" for line in a] + [f"+{line}" for line in b],
        }
        return [hunk], len(b), len(a), True
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    hunks: list[dict[str, Any]] = []
    added = 0
    deleted = 0
    for group in matcher.get_grouped_opcodes(context):
        first = group[0]
        last = group[-1]
        old_start, old_end = first[1], last[2]
        new_start, new_end = first[3], last[4]
        lines: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend(f" {line}" for line in a[i1:i2])
                continue
            if tag in ("replace", "delete"):
                lines.extend(f"-{line}" for line in a[i1:i2])
                deleted += i2 - i1
            if tag in ("replace", "insert"):
                lines.extend(f"+{line}" for line in b[j1:j2])
                added += j2 - j1
        old_count = old_end - old_start
        new_count = new_end - new_start
        hunks.append(
            {
                "old_start": old_start + 1 if old_count else old_start,
                "old_lines": old_count,
                "new_start": new_start + 1 if new_count else new_start,
                "new_lines": new_count,
                "header": "",
                "lines": lines,
            }
        )
    return hunks, added, deleted, False


def count_changes(before: bytes | None, after: bytes | None) -> tuple[int, int, bool]:
    """两侧内容的 (added, deleted, binary)。"""
    old_raw = before or b""
    new_raw = after or b""
    if is_binary(old_raw) or is_binary(new_raw):
        return 0, 0, True
    old = decode_for_diff(old_raw)
    new = decode_for_diff(new_raw)
    if old is None or new is None:
        return 0, 0, True
    _hunks, added, deleted, _coarse = diff_texts(old, new)
    return added, deleted, False


def diff_contents(
    path: str,
    before: bytes | None,
    after: bytes | None,
    *,
    context: int = HUNK_CONTEXT,
    old_path: str | None = None,
) -> dict[str, Any]:
    """两侧原始字节 → 文件对比。None 表示这一侧不存在。"""
    status = (
        "renamed"
        if old_path and old_path != path and before is not None and after is not None
        else _status(before is not None, after is not None)
    )
    base: dict[str, Any] = {
        "path": path,
        "old_path": old_path,
        "status": status,
        "before": before is not None,
        "after": after is not None,
        "hunks": [],
        "added": 0,
        "deleted": 0,
        "coarse": False,
    }
    old_raw = before or b""
    new_raw = after or b""
    if is_binary(old_raw) or is_binary(new_raw):
        return {**base, "kind": "binary"}
    old = decode_for_diff(old_raw)
    new = decode_for_diff(new_raw)
    if old is None or new is None:
        return {**base, "kind": "binary"}
    hunks, added, deleted, coarse = diff_texts(old, new, context)
    return {
        **base,
        "kind": "text",
        "hunks": hunks,
        "added": added,
        "deleted": deleted,
        "coarse": coarse,
    }


def _unquote_git_path(raw: str) -> str:
    """git 对含特殊字符的路径加 C 风格引号；core.quotepath=false 时中文不再转义。"""
    if len(raw) < 2 or not (raw.startswith('"') and raw.endswith('"')):
        return raw
    body = raw[1:-1]
    out = bytearray()
    index = 0
    escapes = {"n": 10, "t": 9, "r": 13, '"': 34, "\\": 92, "a": 7, "b": 8, "f": 12, "v": 11}
    while index < len(body):
        char = body[index]
        if char != "\\" or index + 1 >= len(body):
            out.extend(char.encode("utf-8"))
            index += 1
            continue
        nxt = body[index + 1]
        if nxt in escapes:
            out.append(escapes[nxt])
            index += 2
            continue
        octal = body[index + 1 : index + 4]
        if len(octal) == 3 and all(c in "01234567" for c in octal):
            out.append(int(octal, 8))
            index += 4
            continue
        out.extend(nxt.encode("utf-8"))
        index += 2
    return out.decode("utf-8", errors="replace")


def _strip_side(raw: str) -> str | None:
    path = _unquote_git_path(raw.strip())
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _parse_hunk_header(line: str) -> dict[str, Any] | None:
    if not line.startswith("@@ "):
        return None
    end = line.find(" @@", 3)
    if end < 0:
        return None
    ranges = line[3:end].split(" ")
    if len(ranges) != 2 or not ranges[0].startswith("-") or not ranges[1].startswith("+"):
        return None

    def parse(part: str) -> tuple[int, int]:
        if "," in part:
            start, count = part.split(",", 1)
            return int(start), int(count)
        return int(part), 1

    try:
        old_start, old_lines = parse(ranges[0][1:])
        new_start, new_lines = parse(ranges[1][1:])
    except ValueError:
        return None
    return {
        "old_start": old_start,
        "old_lines": old_lines,
        "new_start": new_start,
        "new_lines": new_lines,
        "header": line[end + 3 :].strip(),
        "lines": [],
    }


def _finish_file(current: dict[str, Any]) -> dict[str, Any]:
    before = current.pop("_before", True)
    after = current.pop("_after", True)
    old_path = current.get("old_path")
    path = current.get("path") or old_path or ""
    current["path"] = path
    current["before"] = before
    current["after"] = after
    if current.get("_renamed") and before and after:
        current["status"] = "renamed"
    else:
        current["status"] = _status(before, after)
        if current["status"] != "renamed" and old_path == path:
            current["old_path"] = None
    current.pop("_renamed", None)
    added = 0
    deleted = 0
    for hunk in current["hunks"]:
        for line in hunk["lines"]:
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                deleted += 1
    current["added"] = added
    current["deleted"] = deleted
    current.setdefault("coarse", False)
    return current


def parse_unified_diff(text: str) -> list[dict[str, Any]]:
    """解析 `git diff` / `git show` 的输出，每个文件一项。"""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    hunk: dict[str, Any] | None = None
    for raw in text.split("\n"):
        line = raw[:-1] if raw.endswith("\r") else raw
        if line.startswith("diff --git "):
            if current is not None:
                files.append(_finish_file(current))
            current = {
                "kind": "text",
                "path": "",
                "old_path": None,
                "hunks": [],
                "_before": True,
                "_after": True,
            }
            hunk = None
            rest = line[len("diff --git ") :]
            # 无引号且不含 " b/" 歧义时，从头部拿路径兜底（纯模式变更没有 ---/+++）。
            if not rest.startswith('"') and " b/" in rest:
                left, right = rest.split(" b/", 1)
                current["old_path"] = _strip_side(left)
                current["path"] = right
            continue
        if current is None:
            continue
        if hunk is None:
            if line.startswith("new file mode"):
                current["_before"] = False
                continue
            if line.startswith("deleted file mode"):
                current["_after"] = False
                continue
            if line.startswith("rename from "):
                current["old_path"] = _unquote_git_path(line[len("rename from ") :])
                current["_renamed"] = True
                continue
            if line.startswith("rename to "):
                current["path"] = _unquote_git_path(line[len("rename to ") :])
                current["_renamed"] = True
                continue
            if line.startswith("Binary files ") or line == "GIT binary patch":
                current["kind"] = "binary"
                if " and /dev/null differ" in line:
                    current["_after"] = False
                if line.startswith("Binary files /dev/null and"):
                    current["_before"] = False
                continue
            if line.startswith("--- "):
                side = _strip_side(line[4:])
                if side is None:
                    current["_before"] = False
                else:
                    current["old_path"] = side
                continue
            if line.startswith("+++ "):
                side = _strip_side(line[4:])
                if side is None:
                    current["_after"] = False
                else:
                    current["path"] = side
                continue
        header = _parse_hunk_header(line)
        if header is not None:
            hunk = header
            current["hunks"].append(hunk)
            continue
        if hunk is None:
            continue
        if line.startswith("\\"):
            continue
        if line[:1] in (" ", "+", "-"):
            hunk["lines"].append(line)
        elif line == "":
            # 空行只可能出现在文件尾部；上下文里的空行 git 会写成一个空格。
            continue
        else:
            hunk = None
    if current is not None:
        files.append(_finish_file(current))
    return files
