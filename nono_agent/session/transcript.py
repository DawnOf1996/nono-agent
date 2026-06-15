"""Transcript 持久化存储 —— 抽象协议 + 本地文件实现。

架构设计：
- TranscriptWriter: Protocol 协议类，定义 transcript 读写的标准接口
- LocalFileTranscriptWriter: 本地 NDJSON 文件实现（默认存储后端）
- 后续可扩展 DB / Redis / S3 等远端存储

差异化写入策略（对应 TS 版）：
- User 消息：await 写入（确保 --resume 可用）
- Assistant 消息：fire-and-forget（不阻塞流式传输）
- Compact 边界：先刷新前面的消息
- Progress/attachment：同步 await

使用 NDJSON 格式（每行一个 JSON 对象）实现高效的追加写入。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import aiofiles

from nono_agent.core.types import Message


# ------------------------------------------------------------------
# 共享工具函数
# ------------------------------------------------------------------

def serialize_message(msg: Message) -> dict[str, Any]:
    """将消息序列化为 JSON 兼容字典 —— 所有 TranscriptWriter 实现共享。

    优先使用 Pydantic 的 model_dump()，回退到手动构造。
    """
    if hasattr(msg, "model_dump"):
        return msg.model_dump()
    return {"type": getattr(msg, "type", "unknown")}


# ------------------------------------------------------------------
# Protocol 协议类
# ------------------------------------------------------------------

@runtime_checkable
class TranscriptWriter(Protocol):
    """Transcript 持久化协议 —— 定义 transcript 读写的标准接口。

    所有存储后端（文件、数据库、Redis 等）都必须实现此协议。
    CLI 或 Server 可在创建 QueryEngine 时显式指定实现：

    用法示例::

        # 默认使用本地文件存储
        engine = QueryEngine(config)

        # 显式指定 Redis 存储（未来扩展）
        engine = QueryEngine(config, transcript_writer=RedisTranscriptWriter(...))
    """

    async def record(self, messages: list[Message]) -> None:
        """记录完整 transcript 状态 —— 全量覆写。"""
        ...

    async def record_append(self, message: Message) -> None:
        """追加单条消息到 transcript。"""
        ...

    async def read(self) -> list[dict[str, Any]]:
        """读取 transcript 中所有消息 —— 返回原始字典列表。"""
        ...

    async def flush(self) -> None:
        """强制刷新缓冲的写入。"""
        ...


# ------------------------------------------------------------------
# 本地文件实现
# ------------------------------------------------------------------

class LocalFileTranscriptWriter:
    """本地 NDJSON 文件 Transcript 写入器（默认存储后端）。

    使用 NDJSON 格式（每行一个 JSON 对象）实现高效的追加写入。
    支持 eager flush 模式，适用于进程可能在轮次之间被杀死的场景（如桌面应用）。
    """

    def __init__(
        self,
        session_file: Path,
        eager_flush: bool = False,
    ) -> None:
        """初始化 LocalFileTranscriptWriter。

        Args:
            session_file: NDJSON 文件路径
            eager_flush: 是否急切刷新（桌面应用模式下建议开启）
        """
        self._session_file = session_file
        self._eager_flush = eager_flush
        self._write_lock = asyncio.Lock()  # 防止并发写入交错

    @property
    def session_file(self) -> Path:
        """会话文件路径。"""
        return self._session_file

    async def record(self, messages: list[Message]) -> None:
        """记录完整 transcript 状态。

        将所有消息写入 NDJSON。在每次重要状态变更后调用。
        使用锁防止并发 fire-and-forget 写入交错。
        """
        async with self._write_lock:
            self._session_file.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self._session_file, "w", encoding="utf-8") as f:
                for msg in messages:
                    line = json.dumps(serialize_message(msg), ensure_ascii=False)
                    await f.write(line + "\n")

    async def record_append(self, message: Message) -> None:
        """追加单条消息到 transcript。

        比 record() 更高效，适用于单消息添加。
        """
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        async with self._write_lock:
            async with aiofiles.open(self._session_file, "a", encoding="utf-8") as f:
                line = json.dumps(serialize_message(message), ensure_ascii=False)
                await f.write(line + "\n")

    async def read(self) -> list[dict[str, Any]]:
        """读取并解析 NDJSON transcript 文件 —— 返回原始消息字典列表。"""
        if not self._session_file.exists():
            return []

        messages: list[dict[str, Any]] = []
        async with aiofiles.open(self._session_file, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # 跳过损坏的行
        return messages

    async def flush(self) -> None:
        """强制刷新缓冲的写入。

        对于进程可能在收到结果消息后立即被杀死的环境很重要（如桌面应用杀死 CLI）。
        """
        # aiofiles 没有显式 flush，但锁确保所有待处理的写入已完成
        async with self._write_lock:
            pass
