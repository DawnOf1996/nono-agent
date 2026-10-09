"""数据目录搬家 —— 把整个 ``.nono-agent`` 搬到新位置，并留下指针。

为什么不运行期热切换：数据目录里有正在写的会话事件流（追加写的文件句柄）、
常驻的 SQLite（``app.db`` 及其 WAL/SHM 伴生文件）、已下载的托管运行时
（Python / Node 的 exe 正被 MCP 子进程占用）。Windows 上这些文件一旦被占住就
搬不走，硬搬的结果是**留下写坏的文件**，而不是「切换失败、状态不变」。
所以这里的流程固定是：

1. 校验目标路径；
2. 写指针文件 —— 万一搬迁中途挂了，下次启动仍然指向**新**目录；
3. 搬家（先复制后删源，出错就把指针改回旧目录）；
4. 提示调用方重启应用。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from nono_agent.paths import (
    CONFIG_DIR_NAME,
    data_dir_pointer_path,
    default_user_home,
    read_data_dir_pointer,
    user_home,
    write_data_dir_pointer,
)

logger = logging.getLogger(__name__)

#: 数据目录自身的权限：仅所有者。会话事件流与凭据都在里面。
DATA_DIR_MODE = 0o700


class DataDirError(RuntimeError):
    """数据目录搬家失败。"""


class DataDirOccupiedError(DataDirError):
    """目标路径已经有内容 / 目标目录被占用。"""


def data_dir() -> Path:
    """当前数据目录。"""
    return user_home()


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _inside(parent: Path, child: Path) -> bool:
    parent_key = os.path.normcase(str(parent)).rstrip("\\/")
    child_key = os.path.normcase(str(child))
    return child_key == parent_key or child_key.startswith(parent_key + os.sep)


def validate_target(raw: str) -> Path:
    """校验并收成目标数据目录的绝对路径。"""
    cleaned = raw.strip()
    if not cleaned:
        raise DataDirError("请选择数据目录")
    try:
        target = Path(cleaned).expanduser().resolve()
    except OSError as exc:
        raise DataDirError("这个路径无效") from exc

    current = data_dir()
    if _same_path(current, target):
        raise DataDirError("新的数据目录和当前一样")
    if current.name == CONFIG_DIR_NAME and _inside(current, target):
        raise DataDirError("新目录不能放在当前数据目录里面")
    # 反向嵌套：新目录必须是当前的祖先，否则切过去就再也够不到旧数据了。
    if _inside(target, current):
        raise DataDirError("新目录不能是当前数据目录的上级目录")
    if target.exists() and not target.is_dir():
        raise DataDirError("这个路径已经是一个文件")
    return target


def _is_empty_or_new(directory: Path) -> bool:
    if not directory.exists():
        return True
    try:
        return not any(directory.iterdir())
    except OSError as exc:
        raise DataDirError(f"读不了目标目录：{exc}") from exc


def _skip_names() -> set[str]:
    """搬家时跳过的名字：指针文件本身留在默认位置。"""
    return {data_dir_pointer_path().name}


def migrate_data_dir(raw: str, *, allow_non_empty: bool = False) -> dict[str, object]:
    """把整个数据目录搬到新位置。

    Args:
        raw: 目标路径。
        allow_non_empty: 目标非空时是否合并。默认拒绝 —— 同名文件覆盖掉旧数据
            是不可逆的，合并语义（哪一份赢）也说不清。

    Returns:
        ``{"data_dir": 新路径, "moved": 文件数, "restart_required": True}``

    Raises:
        DataDirError: 路径不合法 / 目标非空 / 搬迁失败（此时已把指针改回旧目录）。
    """
    target = validate_target(raw)
    if not allow_non_empty and not _is_empty_or_new(target):
        raise DataDirOccupiedError("目标目录不是空的，换一个空目录")

    old = data_dir()
    if not old.is_dir():
        # 还没建过数据目录（首次启动就改设置）—— 直接把指针写过去即可。
        target.mkdir(parents=True, exist_ok=True)
        write_data_dir_pointer(target)
        return {"data_dir": str(target), "moved": 0, "restart_required": True}

    if not target.is_dir():
        try:
            target.mkdir(parents=True)
        except OSError as exc:
            raise DataDirError(f"创建目标目录失败：{exc}") from exc
    try:
        target.chmod(DATA_DIR_MODE)
    except OSError:
        pass

    # 先落指针：复制 + 删除之间一旦进程被杀，下次启动指向新目录，
    # 数据不会丢；反过来（先删后拷）就两头空了。
    write_data_dir_pointer(target)
    moved = 0
    try:
        moved = _move_contents(old, target)
    except Exception as exc:
        logger.exception("Data dir migration failed; restoring pointer")
        try:
            write_data_dir_pointer(old)
        except OSError:
            pass
        if isinstance(exc, OSError):
            raise DataDirError(f"迁移失败：{exc}") from exc
        raise
    return {"data_dir": str(target), "moved": moved, "restart_required": True}


def _move_contents(source: Path, target: Path) -> int:
    """把 source 下所有内容搬到 target，返回搬走的条目数。

    用 ``shutil.copytree`` + ``rmtree`` 而不是 ``shutil.move``：跨磁盘时 move
    退化成「复制 + 删除」，中途失败会留下半份数据，而 copytree 至少保证
    源目录在成功之前始终是完整的。
    """
    skip = _skip_names()
    moved = 0
    for child in sorted(source.iterdir()):
        if child.name in skip:
            continue
        dest = target / child.name
        if child.is_dir():
            shutil.copytree(child, dest, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(child, dest)
        moved += 1
    if moved == 0:
        return 0
    # 复制全部成功后才删源。中途再出错时源目录还是完整的，指针指回旧目录即可。
    for child in sorted(source.iterdir()):
        if child.name in skip:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                logger.warning("Cannot remove old data file %s", child)
    return moved


def revert_pointer() -> Path | None:
    """把指针改回默认位置（数据还在旧目录时的「撤销」）。"""
    if read_data_dir_pointer() is None:
        return None
    write_data_dir_pointer(default_user_home())
    return default_user_home()


__all__ = [
    "DataDirError",
    "DataDirOccupiedError",
    "data_dir",
    "migrate_data_dir",
    "revert_pointer",
    "validate_target",
]
