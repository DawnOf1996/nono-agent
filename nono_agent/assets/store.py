"""资产存储 —— 用户粘贴的图、程序自动截的图统一落在这里。

布局（与 ``sessions/`` 同级，都在数据目录下）::

    <数据目录>/
    ├── sessions/
    └── assets/
        └── 2026/10/10/
            ├── 15-32-05-614-asset_20261010-153205-614_<hash12>_<rand8>-image.png
            ├── 15-32-05-614-…-image.png.asset.json   # 单个资产的元数据
            └── assets.jsonl                            # 当天资产的追加式索引

**为什么落盘，而不是把 base64 塞进会话事件流。** base64 会让会话文件永久
膨胀：一张 1MB 的图在 ``user/input`` 与 ``user/message`` 里各存一份，而且
永远删不掉。改成「文件 + 引用」后，日志里只剩一个小引用，真正的字节在请求
发出的那一刻才从磁盘读回来（见 :mod:`nono_agent.assets.materialize`）。

**资产 ID 是路径的唯一入口。** ID 里带着时间戳与哈希，解析时先过严格正则，
再由 ID 反推目录 —— 客户端传来的相对路径一律不参与定位，因此不存在
``../`` 穿越的可能。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nono_agent.assets.imageinfo import (
    SUPPORTED_MEDIA_TYPES,
    probe_size,
    sniff_media_type,
    suffix_for,
)
from nono_agent.paths import user_home

logger = logging.getLogger(__name__)

#: 单个资产的体积上限（字节）。与前端「图片不能超过 20MB」保持一致。
MAX_ASSET_BYTES = 20 * 1024 * 1024

#: 资产 ID 格式：``asset_<YYYYMMDD>-<HHMMSS>-<毫秒>_<sha256前12位>_<随机8位>``。
#: 严格校验是安全边界的一部分 —— 定位文件全靠它反推目录，不靠客户端路径。
ASSET_ID_RE = re.compile(
    r"^asset_(\d{8})-(\d{6})-(\d{3})_([0-9a-f]{12})_([0-9a-f]{8})$"
)

#: 落盘文件名里承载资产 ID 的那一段：``<时-分-秒-毫秒>-<资产ID>-<原名>``。
#: 资产 ID 本身带连字符（时间戳分段），所以这里按 ID 的真实格式写死，别用宽松的
#: 字符类 —— 否则会在 ID 内部的第一个连字符处截断。
_FILE_NAME_RE = re.compile(
    r"^\d{2}-\d{2}-\d{2}-\d{3}-"
    r"(asset_\d{8}-\d{6}-\d{3}_[0-9a-f]{12}_[0-9a-f]{8})-"
)

#: 文件名里允许保留的字符。其余一律替换成 ``_``。
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

#: 当天索引文件名。
INDEX_FILE_NAME = "assets.jsonl"

#: 单个资产元数据 sidecar 的后缀。
SIDECAR_SUFFIX = ".asset.json"


def default_assets_root() -> Path:
    """资产目录的默认位置 —— 数据目录下的 ``assets/``。

    每次调用都重新解析 ``user_home()``：数据目录在进程内可能因测试或迁移
    指针而变化，缓存下来的绝对路径会把文件写到旧地方去。
    """
    return user_home() / "assets"


@dataclass(frozen=True)
class AssetRef:
    """一个已落盘资产的引用。

    同时是三样东西的载体：消息内容块里的 ``source``、sidecar 里的元数据、
    ``assets.jsonl`` 里的索引行。
    """

    asset_id: str
    kind: str
    source_kind: str
    file_name: str
    media_type: str
    byte_size: int
    sha256: str
    relative_path: str
    absolute_path: str
    created_at_ms: int
    width: int | None = None
    height: int | None = None
    session_id: str | None = None
    turn_id: str | None = None

    def to_source(self) -> dict[str, Any]:
        """转成消息内容块里的 ``source``。

        宽度/高度一起带上：图片块的 token 估算靠它，没有就只剩一个保守兜底值。
        """
        return {
            "type": "asset",
            "asset_id": self.asset_id,
            "media_type": self.media_type,
            "path": self.relative_path,
            "sha256": self.sha256,
            "bytes": self.byte_size,
            "width": self.width,
            "height": self.height,
        }

    def to_record(self) -> dict[str, Any]:
        """转成元数据（sidecar 与索引行共用同一份）。"""
        return {
            "schemaVersion": 1,
            "assetId": self.asset_id,
            "kind": self.kind,
            "sourceKind": self.source_kind,
            "fileName": self.file_name,
            "mimeType": self.media_type,
            "bytes": self.byte_size,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "absolutePath": self.absolute_path,
            "relativePath": self.relative_path,
            "createdAtMs": self.created_at_ms,
            "privacy": "raw-user-asset",
            "diagnosticUploadDefault": "never",
            "sessionId": self.session_id,
            "turnId": self.turn_id,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> AssetRef | None:
        """从元数据还原引用；缺关键字段返回 ``None``。"""
        asset_id = str(record.get("assetId") or "")
        if not ASSET_ID_RE.match(asset_id):
            return None
        return cls(
            asset_id=asset_id,
            kind=str(record.get("kind") or "image"),
            source_kind=str(record.get("sourceKind") or "user-paste"),
            file_name=str(record.get("fileName") or ""),
            media_type=str(record.get("mimeType") or "image/png"),
            byte_size=int(record.get("bytes") or 0),
            sha256=str(record.get("sha256") or ""),
            relative_path=str(record.get("relativePath") or ""),
            absolute_path=str(record.get("absolutePath") or ""),
            created_at_ms=int(record.get("createdAtMs") or 0),
            width=record.get("width"),
            height=record.get("height"),
            session_id=record.get("sessionId"),
            turn_id=record.get("turnId"),
        )


class AssetStore:
    """资产目录的读写入口。

    根目录默认是 ``<数据目录>/assets``；测试可以显式传一个临时目录。
    """

    def __init__(self, root: Path | None = None) -> None:
        self._explicit_root = Path(root) if root is not None else None

    @property
    def root(self) -> Path:
        """资产根目录。"""
        return self._explicit_root if self._explicit_root is not None else default_assets_root()

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def day_dir(self, when: float | None = None) -> Path:
        """某一天对应的目录 —— ``assets/2026/10/10``。"""
        stamp = time.localtime(when if when is not None else time.time())
        return (
            self.root
            / f"{stamp.tm_year:04d}"
            / f"{stamp.tm_mon:02d}"
            / f"{stamp.tm_mday:02d}"
        )

    def save_bytes(
        self,
        data: bytes,
        *,
        source_kind: str = "user-paste",
        session_id: str | None = None,
        turn_id: str | None = None,
        file_name: str | None = None,
    ) -> AssetRef:
        """把图片字节落盘，返回引用。

        Args:
            data: 图片原始字节。
            source_kind: 来源标记（``user-paste`` / ``tool-output`` / …）。
            session_id: 所属会话，写进元数据便于排查。
            turn_id: 所属轮次。
            file_name: 原始文件名（只取名字部分，扩展名按实际类型修正）。

        Raises:
            ValueError: 数据为空、超过体积上限，或不是可识别的图片格式。
        """
        if not data:
            raise ValueError("图片数据为空")
        if len(data) > MAX_ASSET_BYTES:
            raise ValueError(
                f"图片超过 {MAX_ASSET_BYTES // (1024 * 1024)}MB 上限"
                f"（当前 {len(data) // 1024}KB）"
            )
        media_type = sniff_media_type(data)
        if media_type is None or media_type not in SUPPORTED_MEDIA_TYPES:
            raise ValueError("不是可识别的图片格式（支持 PNG / JPEG / GIF / WebP / BMP）")

        digest = hashlib.sha256(data).hexdigest()
        now = time.time()
        local = time.localtime(now)
        millis = int((now - int(now)) * 1000)
        asset_id = (
            f"asset_{local.tm_year:04d}{local.tm_mon:02d}{local.tm_mday:02d}"
            f"-{local.tm_hour:02d}{local.tm_min:02d}{local.tm_sec:02d}-{millis:03d}"
            f"_{digest[:12]}_{secrets.token_hex(4)}"
        )

        day_dir = self.day_dir(now)
        day_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _safe_file_name(file_name) or f"image{suffix_for(media_type)}"
        # 原名的扩展名可能与实际类型不符（``.png`` 里装着 JPEG）——按嗅探结果修正。
        base_name = safe_name.rsplit(".", 1)[0] if "." in safe_name else safe_name
        final_name = f"{base_name}{suffix_for(media_type)}"

        stamp = f"{local.tm_hour:02d}-{local.tm_min:02d}-{local.tm_sec:02d}-{millis:03d}"
        stored_name = f"{stamp}-{asset_id}-{final_name}"
        target = day_dir / stored_name
        try:
            target.write_bytes(data)
        except OSError as exc:
            raise ValueError(f"写入资产目录失败：{exc}") from exc

        size = probe_size(data, media_type)
        ref = AssetRef(
            asset_id=asset_id,
            kind="image",
            source_kind=source_kind,
            file_name=final_name,
            media_type=media_type,
            byte_size=len(data),
            sha256=digest,
            relative_path=_relative_to(self.root, target),
            absolute_path=str(target.resolve()),
            created_at_ms=int(now * 1000),
            width=size[0] if size else None,
            height=size[1] if size else None,
            session_id=session_id,
            turn_id=turn_id,
        )

        record = ref.to_record()
        try:
            (day_dir / f"{stored_name}{SIDECAR_SUFFIX}").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with (day_dir / INDEX_FILE_NAME).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            # 索引写失败不影响资产本身可用 —— 只是少了元数据。
            logger.warning("资产元数据写入失败（%s）：%s", asset_id, exc)
        return ref

    def save_base64(
        self,
        payload: str,
        *,
        source_kind: str = "user-paste",
        session_id: str | None = None,
        turn_id: str | None = None,
        file_name: str | None = None,
    ) -> AssetRef:
        """把 base64 字符串（可带 data URL 前缀）解码后落盘。"""
        raw = payload.strip()
        if raw.startswith("data:"):
            _, _, raw = raw.partition(",")
        raw = "".join(raw.split())
        if not raw:
            raise ValueError("图片数据为空")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"base64 图片数据无法解码：{exc}") from exc
        return self.save_bytes(
            data,
            source_kind=source_kind,
            session_id=session_id,
            turn_id=turn_id,
            file_name=file_name,
        )

    def persist_attachments(
        self,
        attachments: list[dict[str, Any]] | None,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        source_kind: str = "user-paste",
    ) -> list[dict[str, Any]]:
        """把附件里的 base64 图片换成资产引用。

        已经是资产引用的原样保留；认不出来的也原样保留 —— 宁可让 provider
        去报错，也不要在这里把用户的图片悄悄丢掉。
        """
        if not attachments:
            return []
        result: list[dict[str, Any]] = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            if not isinstance(source, dict) or source.get("type") != "base64":
                result.append(item)
                continue
            payload = source.get("data")
            if not isinstance(payload, str) or not payload:
                result.append(item)
                continue
            try:
                ref = self.save_base64(
                    payload,
                    source_kind=source_kind,
                    session_id=session_id,
                    turn_id=turn_id,
                )
            except ValueError as exc:
                logger.warning("附件落盘失败，按原样透传：%s", exc)
                result.append(item)
                continue
            result.append({**item, "source": ref.to_source()})
        return result

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def path_for(self, asset_id: str) -> Path:
        """按资产 ID 定位文件。

        ID 里的时间戳直接给出目录，文件名以 ID 开头 —— 定位完全由 ID 决定，
        客户端给的相对路径不参与，因此不存在路径穿越。

        Raises:
            ValueError: ID 格式非法。
            FileNotFoundError: 文件不在了。
        """
        match = ASSET_ID_RE.match(asset_id.strip())
        if match is None:
            raise ValueError(f"不是合法的资产 ID：{asset_id}")
        date_part, time_part, millis = match.group(1), match.group(2), match.group(3)
        year = int(date_part[0:4])
        month = int(date_part[4:6])
        day = int(date_part[6:8])
        hour = int(time_part[0:2])
        minute = int(time_part[2:4])
        second = int(time_part[4:6])
        day_dir = self.root / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
        prefix = f"{hour:02d}-{minute:02d}-{second:02d}-{millis}-{asset_id}-"
        try:
            entries = sorted(
                item
                for item in day_dir.iterdir()
                if item.name.startswith(prefix) and not item.name.endswith(SIDECAR_SUFFIX)
            )
        except OSError as exc:
            raise FileNotFoundError(f"资产不存在：{asset_id}") from exc
        if not entries:
            raise FileNotFoundError(f"资产不存在：{asset_id}")
        path = entries[0].resolve()
        if not _within(self.root, path):
            raise ValueError(f"资产路径越界：{asset_id}")
        return path

    def read(self, asset_id: str) -> bytes:
        """读出资产字节。"""
        return self.path_for(asset_id).read_bytes()

    def ref_for(self, asset_id: str) -> AssetRef | None:
        """取资产的完整引用（含宽高）；元数据丢了就按文件本身拼一个。"""
        try:
            path = self.path_for(asset_id)
        except (OSError, ValueError):
            return None
        sidecar = path.with_name(path.name + SIDECAR_SUFFIX)
        if sidecar.is_file():
            try:
                record = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = None
            if isinstance(record, dict):
                ref = AssetRef.from_record(record)
                if ref is not None:
                    return ref
        return self._ref_from_file(path, asset_id)

    def asset_id_of(self, path: Path) -> str | None:
        """从资产文件名反查资产 ID；不是资产文件返回 ``None``。"""
        match = _FILE_NAME_RE.match(path.name)
        if match is None:
            return None
        candidate = match.group(1)
        return candidate if ASSET_ID_RE.match(candidate) else None

    def contains(self, path: Path) -> bool:
        """这个路径是不是在资产目录内。"""
        try:
            return _within(self.root, Path(path).resolve())
        except OSError:
            return False

    def resolve_relative(self, relative_path: str) -> Path | None:
        """按资产目录相对路径定位文件（如 ``assets/2026/10/10/xxx.png``）。"""
        cleaned = relative_path.replace("\\", "/").strip()
        if cleaned.startswith("assets/"):
            cleaned = cleaned[len("assets/") :]
        cleaned = cleaned.lstrip("/")
        if not cleaned:
            return None
        candidate = (self.root / cleaned).resolve()
        if not _within(self.root, candidate):
            return None
        return candidate if candidate.is_file() else None

    def _ref_from_file(self, path: Path, asset_id: str) -> AssetRef:
        """元数据缺失时，按文件本身拼一个最小引用。"""
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        media_type = sniff_media_type(data) or "image/png"
        size = probe_size(data, media_type)
        return AssetRef(
            asset_id=asset_id,
            kind="image",
            source_kind="unknown",
            file_name=path.name,
            media_type=media_type,
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            relative_path=_relative_to(self.root, path),
            absolute_path=str(path),
            created_at_ms=int(path.stat().st_mtime * 1000) if path.exists() else 0,
            width=size[0] if size else None,
            height=size[1] if size else None,
        )


# ---------------------------------------------------------------------------
# 进程级单例
# ---------------------------------------------------------------------------

_store: AssetStore | None = None


def get_asset_store() -> AssetStore:
    """进程级资产存储单例。

    单例里**不缓存绝对路径** —— 根目录每次访问时重新解析，所以数据目录
    变了（测试隔离、迁移指针）之后不会写到旧地方。
    """
    global _store
    if _store is None:
        _store = AssetStore()
    return _store


def set_asset_store(store: AssetStore | None) -> None:
    """替换进程级资产存储（测试用；传 ``None`` 恢复默认）。"""
    global _store
    _store = store


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _safe_file_name(name: str | None) -> str:
    """把任意文件名收成安全的 basename。"""
    if not name:
        return ""
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _SAFE_NAME_RE.sub("_", base)
    base = base.lstrip(".") or ""
    return base[:120]


def _relative_to(root: Path, path: Path) -> str:
    """相对资产根目录的路径（正斜杠，资产目录名保留）。"""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


def _within(root: Path, path: Path) -> bool:
    """``path`` 是否在 ``root`` 之内（含 root 自身）。"""
    try:
        root_resolved = root.resolve()
        path_resolved = path.resolve()
    except OSError:
        return False
    if path_resolved == root_resolved:
        return True
    return root_resolved in path_resolved.parents


__all__ = [
    "ASSET_ID_RE",
    "INDEX_FILE_NAME",
    "MAX_ASSET_BYTES",
    "SIDECAR_SUFFIX",
    "AssetRef",
    "AssetStore",
    "default_assets_root",
    "get_asset_store",
    "set_asset_store",
]
