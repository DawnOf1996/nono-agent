"""SSE 辅助：把引擎 AsyncGenerator 接到队列。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def drain_to_queue(agen: Any, out: Any) -> None:
    """把 AsyncGenerator 产出全部倒入队列。"""
    try:
        async for item in agen:
            await out.put(item)
    except Exception:
        logger.exception("submit_message stream failed")
        raise
