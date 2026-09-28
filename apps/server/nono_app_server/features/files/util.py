"""文件服务模块内部工具：文件名校验、内容哈希、编码和预览载荷。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from nono_app_server.common.textdiff import decode_text

_INVALID_FOLDER_CHARS = frozenset('<>:"/\\|?*')

_MAX_MARKDOWN_CHARS = 2_000_000

_MAX_SAVE_CHARS = 4_000_000

_MAX_PREVIEW_BYTES = 5_000_000

_MAX_BINARY_BYTES = 50_000_000

_LIST_LIMIT = 2000

_WATCH_POLL_S = 0.4

_MAX_WATCH_DIRS = 64

_SEARCH_MAX_MATCHES = 200

_SEARCH_MAX_VISITED = 100_000

_UPLOAD_MAX_FILES = 400

_UPLOAD_MAX_FILE_BYTES = 25_000_000

_UPLOAD_MAX_TOTAL_BYTES = 80_000_000

_SEARCH_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".pnpm-store",
        ".yarn",
        ".turbo",
        ".turbopack",
        ".next",
        ".nuxt",
        ".output",
        ".cache",
        ".parcel-cache",
        "coverage",
        "dist",
        "build",
        "out",
        ".umi",
        ".umi-production",
        ".dumi",
        "__pycache__",
        ".venv",
        "venv",
    }
)

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}


def _valid_folder_name(name: str) -> bool:
    if not name or name in {".", ".."}:
        return False
    if any(char in _INVALID_FOLDER_CHARS for char in name):
        return False
    if name.endswith((" ", ".")):
        return False
    return True


def _markdown_file_name(name: str) -> str:
    """只允许在所选目录里写一个 .md 文件，拒绝路径穿越。"""
    cleaned = name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail="请输入文件名")
    if any(char in _INVALID_FOLDER_CHARS for char in cleaned) or cleaned.endswith((" ", ".")):
        raise HTTPException(status_code=400, detail='文件名不能包含 \\ / : * ? " < > |')
    suffix = Path(cleaned).suffix.lower()
    if suffix and suffix != ".md":
        raise HTTPException(status_code=400, detail="只能导出 .md 文件")
    if not suffix:
        cleaned = f"{cleaned}.md"
    return cleaned


def _directory_signature(directory: Path) -> str | None:
    """目录项变化的指纹。文件内容变化不改这一层的列表，所以不看文件 mtime。"""
    try:
        stat = directory.stat()
    except OSError:
        return None
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _safe_upload_relative(raw: str) -> Path:
    cleaned = raw.replace("\\", "/").strip()
    if not cleaned or cleaned.startswith("/") or ":" in cleaned[:3]:
        raise HTTPException(status_code=400, detail="上传路径无效")
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    invalid = any(part in {".."} or part.endswith((" ", ".")) for part in parts)
    if not parts or invalid:
        raise HTTPException(status_code=400, detail="上传路径无效")
    if any(char in _INVALID_FOLDER_CHARS for part in parts for char in part):
        raise HTTPException(status_code=400, detail="上传路径无效")
    return Path(*parts)


def _content_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _preview_payload(
    path: Path,
    kind: str,
    content: str | None,
    size: int,
    content_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "path": str(path),
        "name": path.name,
        "kind": kind,
        "content": content,
        "size": size,
        "truncated": False,
        "hash": content_hash,
    }


def _text_encoding(data: bytes) -> str | None:
    """沿用文件原来的编码写回；判断不出文本编码时返回 None。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if decode_text(data) is None:
        return None
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gb18030"
