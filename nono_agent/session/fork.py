"""会话分叉 —— 在事件流某个 seq 处剪一刀复制成新会话。

事件流是 append-only 的，所以「从某一步另起一条时间线」只是把前缀复制到新会话，
并在新会话元信息里记下来源，形成谱系。
"""

from __future__ import annotations

from nono_agent.session.header import make_header
from nono_agent.session.transcript import TranscriptWriter


async def fork_transcript(
    source: TranscriptWriter,
    target: TranscriptWriter,
    boundary_seq: int,
    new_session_id: str,
) -> None:
    """把 ``source`` 中 ``seq <= boundary_seq`` 的事件复制到 ``target``。

    ``target`` 重新分配自己的 seq（从 1 起），并写入 ``parent_session``
    指向来源会话的元信息。

    Args:
        source: 来源会话事件流。
        target: 目标（新）会话事件流，应为空。
        boundary_seq: 剪切点（含此 seq）。
        new_session_id: 新会话 ID。
    """
    source_header = await source.read_header()
    for event in await source.read_events():
        if event.seq <= boundary_seq:
            await target.append_event(event)
    await target.write_header(
        make_header(
            new_session_id,
            cwd=source_header.cwd if source_header else "",
            parent_session=source_header.session_id if source_header else None,
            agent_preset_id=source_header.agent_preset_id if source_header else None,
        )
    )


__all__ = ["fork_transcript"]
