"""约定目录名 —— 用户运行时与项目配置共用 ``.nono-agent``。

用户侧：``<数据目录>/{config.yaml,credentials,sessions/,memory/,settings.json,mcp.json,rules/,...}``
项目侧：``<cwd>/.nono-agent/{config.yaml,settings.json,rules/,skills/,agents/,plugins/,hooks.json}``

**数据目录**就是 ``~/.nono-agent`` 本身，设置里可以整体搬家。搬家之后进程内
所有派生路径（config.yaml / credentials / sessions / memory / app.db …）都得
跟着走，所以这个目录在一个进程生命周期内**只有一个值**：启动时解析一次，
之后不再变。切换靠「写指针 → 迁移 → 重启」三步，不做运行期热切换 ——
SQLite、托管运行时和正在写的会话文件在 Windows 上都被锁着，热切换只会
留下写坏的文件（迁移本身也会因为拿不到句柄而失败）。

指针文件固定放在**默认**位置 ``~/.nono-agent/data-dir.json``。数据目录搬走
之后这个锚点还在，下次启动才知道该去哪儿找。它本身不参与迁移。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR_NAME = ".nono-agent"

#: 记录「数据目录搬到了哪」的指针文件名，放在默认位置，不随数据目录迁移。
POINTER_FILE_NAME = "data-dir.json"

#: 环境变量优先于指针文件 —— 给测试和临时运行用。
ENV_DATA_DIR = "NONO_DATA_DIR"

# 解析结果缓存。user_home() 在很多模块的导入期被调用，不能每次都读一遍磁盘。
_cached_home: Path | None = None


def default_user_home() -> Path:
    """默认位置 ``~/.nono-agent`` —— 永远可算，不受迁移影响。"""
    return Path.home() / CONFIG_DIR_NAME


def data_dir_pointer_path() -> Path:
    """数据目录指针文件路径（固定在默认位置）。"""
    return default_user_home() / POINTER_FILE_NAME


def read_data_dir_pointer() -> Path | None:
    """读指针文件。没写过、损坏、或者内容不是路径时返回 None。"""
    try:
        raw = data_dir_pointer_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    value = data.get("data_dir") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value.strip()).expanduser()


def write_data_dir_pointer(path: Path) -> None:
    """写指针文件，让下次启动知道数据目录搬到了哪。"""
    pointer = data_dir_pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps({"data_dir": str(path)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def reset_user_home_cache() -> None:
    """丢弃缓存 —— 测试改了环境变量 / 指针文件后要显式调一次。"""
    global _cached_home
    _cached_home = None


def user_home() -> Path:
    """当前数据目录 ``<数据目录>``。

    解析顺序：``NONO_DATA_DIR`` 环境变量 > 默认位置的指针文件 > 默认位置。
    结果缓存 —— 进程内不允许换数据目录（见模块文档）。
    """
    global _cached_home
    if _cached_home is not None:
        return _cached_home
    _cached_home = _resolve_user_home()
    return _cached_home


def _resolve_user_home() -> Path:
    env = os.environ.get(ENV_DATA_DIR, "").strip()
    if env:
        return Path(env).expanduser()
    pointer = read_data_dir_pointer()
    if pointer is not None:
        return pointer
    legacy = _legacy_data_dir_from_settings()
    if legacy is not None:
        write_data_dir_pointer(legacy)
        return legacy
    return default_user_home()


def _legacy_data_dir_from_settings() -> Path | None:
    """一次性兼容：老版本把会话目录写进默认位置 settings.json 的 ``session_dir``。

    只有那个目录就叫 ``sessions`` 时才反推它的父目录 —— 否则父目录可能是磁盘根，
    反推出的「数据目录」毫无意义。这种情况宁可不认，交给用户在设置里手动指定。
    """
    settings = default_user_home() / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("session_dir") if isinstance(data, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw.strip()).expanduser()
    if candidate.name != "sessions":
        return None
    return candidate.parent


def project_dir(root: str | Path) -> Path:
    """项目级 ``<root>/.nono-agent`` 根目录。"""
    return Path(root) / CONFIG_DIR_NAME


def is_data_dir(path: Path) -> bool:
    """这个 ``.nono-agent`` 目录是不是**用户数据目录**（当前或历史位置）。

    向上 walk 找项目配置时必须用它挡一下，否则数据目录搬过家之后，
    老位置 ``~/.nono-agent`` 会被当成「某个项目的配置」捡走 —— walk 一路走到
    ``C:\\Users\\<你>`` 就会撞上它，于是搬了家的用户会突然读到一份陈旧的
    ``config.yaml``（里面可能还留着别的机器上配的模型和密钥）。
    数据目录可以落在项目树的任意位置，所以判定不能只看「是不是当前那个」。
    """
    resolved = Path(path).resolve()
    return os.path.normcase(str(resolved)) in _data_dir_candidates()


def _data_dir_candidates() -> set[str]:
    """所有算作「用户数据目录」的路径（含默认位置与当前实际位置）。"""
    candidates = {str(default_user_home()), str(user_home())}
    legacy = _legacy_data_dir_from_settings()
    if legacy is not None:
        candidates.add(str(legacy))
    return {os.path.normcase(item) for item in candidates}


def resolve_session_cwd(cwd: str | Path | None) -> str:
    """把工作目录收成绝对路径。

    会话 ``.header.json`` 与引擎沙箱都不能依赖 ``.`` 这类相对路径：
    resume 时进程 cwd 可能已经变了。
    """
    raw = str(cwd).strip() if cwd is not None else ""
    path = Path(raw).expanduser() if raw else Path.cwd()
    return str(path.resolve())
