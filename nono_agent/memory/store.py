"""MemoryStore —— 跨会话记忆存储协议 + 文件系统（memdir）实现。

memdir 约定：一个目录，每个 ``.md`` 文件是一条记忆，frontmatter 可选
``type`` 字段做类型约束。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from nono_agent.memory.models import Memory
from nono_agent.skills.loader import parse_skill_markdown  # 复用 frontmatter 解析


@runtime_checkable
class MemoryStore(Protocol):
    """跨会话记忆存储协议。"""

    def load(self) -> list[Memory]:
        """加载全部记忆。"""
        ...

    def add(self, name: str, content: str, type_: str | None = None) -> Memory:
        """新增或覆写一条记忆。"""
        ...

    def delete(self, name: str) -> bool:
        """删除一条记忆。"""
        ...


class FileSystemMemoryStore:
    """文件系统 memdir 记忆存储。"""

    def __init__(self, memdir: str | Path | None = None) -> None:
        self._memdir = Path(memdir) if memdir is not None else Path.home() / ".nono" / "memory"

    @property
    def memdir(self) -> Path:
        return self._memdir

    def load(self) -> list[Memory]:
        memories: list[Memory] = []
        if not self._memdir.is_dir():
            return memories
        for file in sorted(self._memdir.glob("*.md")):
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, body = parse_skill_markdown(text)
            body = body.strip()
            if not body:
                continue
            memories.append(
                Memory(
                    name=file.stem,
                    content=body,
                    type=str(meta["type"]) if meta.get("type") else None,
                    source=str(file),
                )
            )
        return memories

    def add(self, name: str, content: str, type_: str | None = None) -> Memory:
        self._memdir.mkdir(parents=True, exist_ok=True)
        header = f"---\ntype: {type_}\n---\n" if type_ else ""
        file = self._memdir / f"{name}.md"
        file.write_text(header + content, encoding="utf-8")
        return Memory(name=name, content=content, type=type_, source=str(file))

    def delete(self, name: str) -> bool:
        file = self._memdir / f"{name}.md"
        if file.exists():
            file.unlink()
            return True
        return False
