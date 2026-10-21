"""资产存储 —— 图片等二进制内容的统一落盘处。

对话里的图片不再以 base64 形态留在会话事件流里，而是落成磁盘文件，
会话里只留一个资产引用；字节在请求发出的那一刻才从磁盘读回来。

- :mod:`nono_agent.assets.store` —— 资产目录的读写（落盘、定位、元数据）
- :mod:`nono_agent.assets.imageinfo` —— 图片类型与尺寸探测
- :mod:`nono_agent.assets.materialize` —— 请求前把资产引用还原成 base64

对外的主要入口是 :func:`get_asset_store`。
"""

from __future__ import annotations

from nono_agent.assets.imageinfo import (
    SUPPORTED_MEDIA_TYPES,
    probe_size,
    sniff_media_type,
    suffix_for,
)
from nono_agent.assets.store import (
    ASSET_ID_RE,
    INDEX_FILE_NAME,
    MAX_ASSET_BYTES,
    SIDECAR_SUFFIX,
    AssetRef,
    AssetStore,
    default_assets_root,
    get_asset_store,
    set_asset_store,
)

__all__ = [
    "ASSET_ID_RE",
    "INDEX_FILE_NAME",
    "MAX_ASSET_BYTES",
    "SIDECAR_SUFFIX",
    "SUPPORTED_MEDIA_TYPES",
    "AssetRef",
    "AssetStore",
    "default_assets_root",
    "get_asset_store",
    "probe_size",
    "set_asset_store",
    "sniff_media_type",
    "suffix_for",
]
