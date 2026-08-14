"""CheckpointStore —— 编辑快照/回滚协议 + 文件系统实现。

会话内对一组文件做快照（内容 + 是否存在的状态），支持 rewind 回滚。
供编辑回滚（§2.11）与未来 Checkpoint/Rewind 工具使用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class CheckpointStore(Protocol):
    """编辑快照存储协议。"""

    def snapshot(self, paths: list[str]) -> str:
        """对给定路径做快照并返回 id。"""
        ...

    def rewind(self, checkpoint_id: str) -> None:
        """回滚到指定快照。"""
        ...

    def list_ids(self) -> list[str]:
        """列出所有快照 id（按创建顺序）。"""
        ...

    def drop(self, checkpoint_id: str) -> bool:
        """删除快照。"""
        ...


class FileCheckpointStore:
    """文件系统快照存储（工作区相对路径）。"""

    def __init__(self, cwd: str | Path) -> None:
        self._cwd = Path(cwd).resolve()
        self._checkpoints: dict[str, dict[str, str | None]] = {}
        self._order: list[str] = []
        self._counter = 0

    def snapshot(self, paths: list[str]) -> str:
        checkpoint_id = f"cp_{self._counter}"
        self._counter += 1

        snap: dict[str, str | None] = {}
        for path in paths:
            resolved = self._resolve(path)
            if resolved.exists():
                snap[path] = resolved.read_text(encoding="utf-8", errors="replace")
            else:
                snap[path] = None  # 快照时不存在

        self._checkpoints[checkpoint_id] = snap
        self._order.append(checkpoint_id)
        return checkpoint_id

    def rewind(self, checkpoint_id: str) -> None:
        snap = self._checkpoints.get(checkpoint_id)
        if snap is None:
            raise KeyError(f"Unknown checkpoint: {checkpoint_id}")

        for path, content in snap.items():
            resolved = self._resolve(path)
            if content is None:
                if resolved.exists():
                    resolved.unlink()
            else:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(content, encoding="utf-8")

    def list_ids(self) -> list[str]:
        return list(self._order)

    def drop(self, checkpoint_id: str) -> bool:
        if checkpoint_id in self._checkpoints:
            del self._checkpoints[checkpoint_id]
            self._order.remove(checkpoint_id)
            return True
        return False

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self._cwd / p
        return p.resolve()
