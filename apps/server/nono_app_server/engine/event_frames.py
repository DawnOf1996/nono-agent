"""会话事件流的帧缓存与分片裁剪。

点开一个会话要走一遍：解压事件流 → 逐行 Pydantic 解析 → 脱敏 → 重新序列化。
长会话动辄几万条事件，光这四步就要两秒多，而同一会话会被反复打开。

这里把「脱敏 + 序列化」的产物按 (文件, mtime, 大小, 裁剪模式) 缓存下来：
文件一被追加写，mtime/大小就会变，缓存自然失效，不用额外的失效逻辑。
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from nono_agent.session.events import EVENT_ASSISTANT_CHUNK
from nono_agent.session.redact import redact_events

# 缓存多少个会话的帧。单个会话可能上万帧，条目数得封顶，否则长会话挨个打开
# 一次就能把内存吃光。
CACHE_MAX_ENTRIES = 8

# chunks=thinking：只保留思考增量。assistant/chunk 里九成以上是 text_delta，
# 而正文在 assistant/message 里已经有完整一份，客户端对 text_delta 直接丢弃
# （见 apps/web/src/api/sse.ts 的 extractThinkingDelta）。
CHUNK_MODE_ALL = "all"
CHUNK_MODE_THINKING = "thinking"

_cache: OrderedDict[tuple[str, float, int, str], list[tuple[int, str]]] = OrderedDict()


def _is_thinking_chunk(event: Any) -> bool:
    """这条 ``assistant/chunk`` 是否是思考增量。

    判定条件与前端 ``extractThinkingDelta`` 保持一致：``data.chunk`` 是
    ``content_block_delta`` 且 ``delta.type == "thinking_delta"``。
    """
    data = event.data
    chunk = data.get("chunk") if isinstance(data, dict) else None
    if not isinstance(chunk, dict):
        return False
    if chunk.get("type") != "content_block_delta":
        return False
    delta = chunk.get("delta")
    return isinstance(delta, dict) and delta.get("type") == "thinking_delta"


def keep_event(event: Any, chunk_mode: str) -> bool:
    if chunk_mode != CHUNK_MODE_THINKING:
        return True
    if event.type != EVENT_ASSISTANT_CHUNK:
        return True
    return _is_thinking_chunk(event)


def _cache_key(session_file: Path, chunk_mode: str) -> tuple[str, float, int, str] | None:
    try:
        stat = session_file.stat()
    except OSError:
        return None
    return (str(session_file), stat.st_mtime, stat.st_size, chunk_mode)


def cached_frames(session_file: Path, chunk_mode: str) -> list[tuple[int, str]] | None:
    key = _cache_key(session_file, chunk_mode)
    if key is None:
        return None
    frames = _cache.get(key)
    if frames is None:
        return None
    _cache.move_to_end(key)
    return frames


def store_frames(
    session_file: Path,
    chunk_mode: str,
    frames: list[tuple[int, str]],
) -> None:
    key = _cache_key(session_file, chunk_mode)
    if key is None:
        return
    _cache[key] = frames
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


def clear_cache() -> None:
    """测试用：丢掉全部缓存。"""
    _cache.clear()


def build_frames(
    events: Iterable[Any],
    chunk_mode: str = CHUNK_MODE_ALL,
) -> list[tuple[int, str]]:
    """脱敏 + 裁剪 + 序列化成 ``(seq, json)``。

    先脱敏再裁剪：脱敏要按事件内容改写，裁剪只是丢弃，两者不冲突。
    """
    kept = [event for event in events if keep_event(event, chunk_mode)]
    return [(event.seq, event.model_dump_json()) for event in redact_events(kept)]


def frames_to_sse(frames: Iterable[tuple[int, str]]) -> Iterable[dict[str, str]]:
    for seq, payload in frames:
        yield {
            "event": "session_event",
            "id": str(seq),
            "data": payload,
        }


def last_seq(frames: list[tuple[int, str]]) -> int:
    return frames[-1][0] if frames else 0


def done_payload(last: int) -> str:
    return json.dumps({"last_seq": last})
