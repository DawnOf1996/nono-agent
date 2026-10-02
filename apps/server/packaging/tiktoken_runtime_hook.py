"""PyInstaller runtime hook：把 tiktoken 词表指向随包分发的那份。

tiktoken 的 BPE 词表**不在 wheel 里**，``get_encoding()`` 首次调用会去
openaipublic.blob.core.windows.net 下载。桌面端经常离线或走公司代理，
那就永远拿不到精确计数，只能退回 ``len//4`` 启发式（对工具 schema 低估约 40%）。

构建期（scripts/build_sidecar.py）已经把词表下载好并放进 ``_MEIPASS/tiktoken_cache``，
这里在主逻辑跑起来之前把 ``TIKTOKEN_CACHE_DIR`` 指过去 —— tiktoken 命中缓存
就不会走网络了。

``setdefault`` 是有意的：用户显式设了环境变量就听用户的。
"""

from __future__ import annotations

import os
import sys

_BASE = getattr(sys, "_MEIPASS", None)
if _BASE:
    _CACHE = os.path.join(_BASE, "tiktoken_cache")
    if os.path.isdir(_CACHE):
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", _CACHE)
