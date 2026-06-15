"""媒体大小错误恢复。

处理与超大图片或媒体内容相关的错误。
这类错误通常无法自动恢复 —— 用户需要提供更小的媒体或移除附件。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 最大图片大小（字节）—— 因 Provider 而异
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20MB


def is_image_error(error: Exception | dict[str, Any]) -> bool:
    """检查错误是否与图片/媒体大小相关。"""
    error_str = str(error).lower()
    return any(
        keyword in error_str
        for keyword in ("image", "media", "file too large", "content too large")
    )


def create_image_error_message(error: Exception | dict[str, Any]) -> str:
    """创建用户友好的图片大小错误提示。"""
    return (
        "The image or media content you provided is too large. "
        "Please reduce the image size or resolution and try again. "
        f"Maximum supported size: {DEFAULT_MAX_IMAGE_BYTES // (1024*1024)}MB."
    )
