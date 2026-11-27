"""图片类型与尺寸探测 —— 只看内容，不信扩展名。

落盘时用它定 ``media_type`` 与宽高：

- **类型**必须准。扩展名与内容各说各话时，provider 会拿一份 PNG 去解 JPEG，
  报出来的错却指向一个看起来完全无辜的参数。
- **宽高**决定图片块的 token 估算（Anthropic 按 ``(宽 x 高) / 750`` 计价）。
  估算偏高会让压缩提前触发，偏低会让请求直接撞上 context 上限。

探测不出来就返回 ``None`` —— 尺寸只是估算的输入，猜错比没有更糟。
"""

from __future__ import annotations

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_GIF87_MAGIC = b"GIF87a"
_GIF89_MAGIC = b"GIF89a"

#: media type → 落盘后缀。认不出的类型退回 ``.png``（与嗅探失败时的兜底一致）。
SUFFIX_BY_MEDIA_TYPE: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}

#: 允许落盘的图片类型 —— 上传与工具读图都只认这些。
SUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset(SUFFIX_BY_MEDIA_TYPE)


def sniff_media_type(data: bytes) -> str | None:
    """按魔数判定图片类型；认不出返回 ``None``。"""
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if data.startswith(_GIF87_MAGIC) or data.startswith(_GIF89_MAGIC):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def suffix_for(media_type: str) -> str:
    """media type → 文件后缀。"""
    return SUFFIX_BY_MEDIA_TYPE.get(media_type, ".png")


def probe_size(data: bytes, media_type: str) -> tuple[int, int] | None:
    """探测图片宽高；认不出返回 ``None``。

    Args:
        data: 图片字节。
        media_type: 已判定（或已声明）的图片类型。

    Returns:
        ``(宽, 高)``；无法确定时为 ``None``。
    """
    if media_type == "image/png":
        return _png_size(data)
    if media_type == "image/jpeg":
        return _jpeg_size(data)
    if media_type == "image/gif":
        return _gif_size(data)
    if media_type == "image/webp":
        return _webp_size(data)
    if media_type == "image/bmp":
        return _bmp_size(data)
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    # 8 字节签名 + 4 字节长度 + 4 字节 "IHDR" → 宽高各 4 字节大端。
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    if width <= 0 or height <= 0:
        return None
    return width, height


def _bmp_size(data: bytes) -> tuple[int, int] | None:
    # BITMAPINFOHEADER：宽高各 4 字节有符号，偏移 18。
    if len(data) < 26:
        return None
    width = int.from_bytes(data[18:22], "little", signed=True)
    height = int.from_bytes(data[22:26], "little", signed=True)
    if width <= 0 or height <= 0:
        return None
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """扫 JPEG 段找 SOF 标记 —— 尺寸藏在帧头里。"""
    index = 2
    length = len(data)
    while index + 9 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        # 填充字节 / 帧 / 重启标记：没有段长，直接跳过两字节。
        if marker == 0xFF or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if marker == 0xD8 or marker == 0xD9:
            index += 2
            continue
        if index + 4 > length:
            return None
        seg_len = int.from_bytes(data[index + 2 : index + 4], "big")
        # SOF0..SOF15，排除 DHT(C4) / JPG(C8) / DAC(CC)。
        if marker in (
            0xC0, 0xC1, 0xC2, 0xC3,
            0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB,
            0xCD, 0xCE, 0xCF,
        ):
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            if width <= 0 or height <= 0:
                return None
            return width, height
        if seg_len <= 0:
            return None
        index += 2 + seg_len
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    """WebP 尺寸在首个 chunk 里，分三种编码。"""
    if len(data) < 30:
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        # 扩展格式：画布宽高各 24 位，存的是「减一」。
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
    elif chunk == b"VP8 ":
        # 有损：帧头 3 字节 + 同步码 3 字节之后，宽高各 14 位。
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
    elif chunk == b"VP8L":
        # 无损：签名字节之后 4 字节里，14 位宽 + 14 位高。
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
    else:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


__all__ = [
    "SUPPORTED_MEDIA_TYPES",
    "SUFFIX_BY_MEDIA_TYPE",
    "probe_size",
    "sniff_media_type",
    "suffix_for",
]
