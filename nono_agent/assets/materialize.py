"""资产水合 —— 把消息里的资产引用还原成 base64 图片块。

图片改成落盘之后，会话状态与事件流里留的是**引用**
（``source.type == "asset"``），真正的字节在请求发出的这一刻才从磁盘读回来。

**为什么放在 provider 调用前，而不是预处理管线里。** 预处理管线跑的是
``MessageStore.snapshot_for_request()`` 的深拷贝，但**本轮的用户消息是在管线
之后才追加进请求列表的**（见 ``nono_agent.core.loop``）—— 挂在管线上，本轮
贴进来的图在第一次请求里就丢了。所以水合放在「消息全部就位、即将发给
provider」的那一刻：历史消息与本轮消息一次覆盖。

**为什么不动会话状态。** 传给 provider 的消息列表是深拷贝，水合只改副本；
store 与事件流里留的始终是引用，磁盘上才有一份字节。
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from nono_agent.assets.imageinfo import sniff_media_type
from nono_agent.assets.store import AssetStore, get_asset_store
from nono_agent.core.types import Message

logger = logging.getLogger(__name__)


def materialize_asset_images(
    messages: list[Message],
    store: AssetStore | None = None,
) -> int:
    """把消息里的资产引用换成 base64 图片块。

    Args:
        messages: 请求用的消息列表（深拷贝，可以就地改）。
        store: 资产存储；None 时用进程级单例。

    Returns:
        被水合的图片块数量。
    """
    if not messages:
        return 0
    active = store or get_asset_store()
    hydrated = 0
    for msg in messages:
        content = getattr(msg, "message", {}).get("content")
        if not isinstance(content, list):
            continue
        for index, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            source = block.get("source")
            if not isinstance(source, dict) or source.get("type") != "asset":
                continue
            replacement = _hydrate(active, source)
            if replacement is not None:
                content[index] = replacement
                hydrated += 1
    return hydrated


def _hydrate(
    store: AssetStore,
    source: dict[str, Any],
) -> dict[str, Any] | None:
    """读出资产字节并拼成 base64 图片块。

    资产读不出来（文件被删了、磁盘满了）时，把这个块换成一句说明文字 ——
    宁可让模型知道"图没了"，也不要发一个结构不合法的图片块给 provider。
    """
    asset_id = str(source.get("asset_id") or "")
    try:
        data = store.read(asset_id)
    except (OSError, ValueError) as exc:
        logger.warning("资产 %s 读取失败：%s", asset_id, exc)
        return {
            "type": "text",
            "text": f"[图片不可用：{asset_id}（{exc}）]",
        }
    media_type = (
        str(source.get("media_type") or "")
        or sniff_media_type(data)
        or "image/png"
    )
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


__all__ = ["materialize_asset_images"]
