"""数据目录：解析、搬家，以及整体搬迁后各处派生路径是否跟着走。

全程走真实解析链路（``NONO_DATA_DIR`` > 指针文件 > 默认位置），用指针文件
把「当前数据目录」指向 tmp 下的目录，而不是去 monkeypatch 各个模块里的
``user_home`` 绑定 —— 后者会和 conftest 的 autouse 夹具打架，而且改的是
导入期的符号，测不出「重新解析之后派生路径跟不跟着走」这件事。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nono_agent import paths as paths_module
from nono_agent.datadir import (
    DataDirError,
    DataDirOccupiedError,
    migrate_data_dir,
    suggested_data_dir,
    validate_target,
)


@pytest.fixture(autouse=True)
def _isolate_pointer(tmp_path, monkeypatch):
    """把「默认位置」和解析缓存都拉到 tmp，绝不碰真实的 ~/.nono-agent。

    conftest 已经用 ``NONO_DATA_DIR`` 指到 tmp 了；这里再把默认位置也挪走，
    才能测「指针文件 / 旧 settings.json 这两条兜底分支」。
    """
    config = tmp_path / "fake-home" / paths_module.CONFIG_DIR_NAME
    config.mkdir(parents=True)
    monkeypatch.setattr(paths_module, "default_user_home", lambda: config)
    monkeypatch.setattr(
        paths_module, "data_dir_pointer_path", lambda: config / paths_module.POINTER_FILE_NAME
    )
    # 屏蔽 conftest 的环境变量，本文件要测的就是它之外的解析顺序。
    monkeypatch.delenv(paths_module.ENV_DATA_DIR, raising=False)
    paths_module.reset_user_home_cache()
    yield
    paths_module.reset_user_home_cache()


def _point_at(directory: Path) -> Path:
    """把「当前数据目录」指向 directory 并清掉解析缓存。"""
    paths_module.write_data_dir_pointer(directory)
    paths_module.reset_user_home_cache()
    return directory


# ------------------------------------------------------------------
# 解析
# ------------------------------------------------------------------

def test_defaults_to_home_dir() -> None:
    assert paths_module.user_home() == paths_module.default_user_home()
    assert paths_module.read_data_dir_pointer() is None


def test_pointer_redirects_user_home(tmp_path) -> None:
    target = tmp_path / "moved"
    target.mkdir()
    _point_at(target)

    assert paths_module.user_home() == target


def test_env_var_beats_pointer(tmp_path, monkeypatch) -> None:
    pointer = tmp_path / "from-pointer"
    pointer.mkdir()
    _point_at(pointer)

    env_target = tmp_path / "from-env"
    monkeypatch.setenv(paths_module.ENV_DATA_DIR, str(env_target))
    paths_module.reset_user_home_cache()

    assert paths_module.user_home() == env_target


def test_corrupt_pointer_falls_back_to_default() -> None:
    paths_module.data_dir_pointer_path().write_text("{ not json", encoding="utf-8")
    paths_module.reset_user_home_cache()

    assert paths_module.read_data_dir_pointer() is None
    assert paths_module.user_home() == paths_module.default_user_home()


def test_blank_pointer_value_falls_back_to_default() -> None:
    paths_module.data_dir_pointer_path().write_text(
        json.dumps({"data_dir": "   "}), encoding="utf-8"
    )
    paths_module.reset_user_home_cache()

    assert paths_module.user_home() == paths_module.default_user_home()


def test_result_is_cached_until_reset(tmp_path) -> None:
    assert paths_module.user_home() == paths_module.default_user_home()
    # 进程内不允许换数据目录：解析一次就固定下来。
    paths_module.write_data_dir_pointer(tmp_path / "elsewhere")
    assert paths_module.user_home() == paths_module.default_user_home()

    paths_module.reset_user_home_cache()
    assert paths_module.user_home() == tmp_path / "elsewhere"


def test_legacy_session_dir_setting_is_adopted_once(tmp_path) -> None:
    """老版本把会话目录写进 settings.json —— 反推父目录并落成指针。"""
    legacy = tmp_path / "legacy" / paths_module.CONFIG_DIR_NAME
    (legacy / "sessions").mkdir(parents=True)
    (paths_module.default_user_home() / "settings.json").write_text(
        json.dumps({"session_dir": str(legacy / "sessions")}), encoding="utf-8"
    )
    paths_module.reset_user_home_cache()

    assert paths_module.user_home() == legacy
    # 认领过一次之后指针落在默认位置，下次不再靠猜。
    assert paths_module.read_data_dir_pointer() == legacy


def test_legacy_session_dir_outside_config_dir_is_ignored(tmp_path) -> None:
    """会话目录的父目录不叫 .nono-agent 时不认 —— 反推出的数据目录没意义。"""
    settings = paths_module.default_user_home() / "settings.json"
    settings.write_text(
        json.dumps({"session_dir": str(tmp_path / "Documents" / "chats")}), encoding="utf-8"
    )
    paths_module.reset_user_home_cache()

    assert paths_module.user_home() == paths_module.default_user_home()
    assert paths_module.read_data_dir_pointer() is None


# ------------------------------------------------------------------
# 目标校验
# ------------------------------------------------------------------

def test_validate_rejects_same_dir() -> None:
    with pytest.raises(DataDirError, match="一样"):
        validate_target(str(paths_module.default_user_home()))


def test_validate_rejects_nested_target(tmp_path) -> None:
    current = _point_at(tmp_path / paths_module.CONFIG_DIR_NAME)
    current.mkdir(exist_ok=True)

    with pytest.raises(DataDirError, match="里面"):
        validate_target(str(current / "inner"))


def test_validate_rejects_parent_target(tmp_path) -> None:
    current = _point_at(tmp_path / "a" / paths_module.CONFIG_DIR_NAME)
    current.mkdir(parents=True)

    with pytest.raises(DataDirError, match="上级"):
        validate_target(str(current.parent))


def test_validate_rejects_blank() -> None:
    with pytest.raises(DataDirError):
        validate_target("   ")


def test_validate_rejects_existing_file(tmp_path) -> None:
    occupied = tmp_path / "afile"
    occupied.write_text("x", encoding="utf-8")

    with pytest.raises(DataDirError, match="已经是一个文件"):
        validate_target(str(occupied))


# ------------------------------------------------------------------
# 搬家
# ------------------------------------------------------------------

def _seed(directory: Path) -> None:
    (directory / "sessions").mkdir(parents=True)
    (directory / "sessions" / "abc.jsonl.zst").write_bytes(b"events")
    (directory / "config.yaml").write_text("providers: {}", encoding="utf-8")
    (directory / "credentials").write_text("KEY=value\n", encoding="utf-8")


def test_migrate_moves_whole_data_dir(tmp_path) -> None:
    current = _point_at(tmp_path / paths_module.CONFIG_DIR_NAME)
    _seed(current)
    target = tmp_path / "elsewhere"

    result = migrate_data_dir(str(target))

    assert result["restart_required"] is True
    assert Path(result["data_dir"]) == target
    # 会话、配置、凭据一起走 —— 这就是「整个 .nono-agent 搬家」的含义。
    assert (target / "sessions" / "abc.jsonl.zst").read_bytes() == b"events"
    assert (target / "config.yaml").is_file()
    assert (target / "credentials").is_file()
    # 源目录清空了。
    assert not (current / "sessions").exists()
    assert paths_module.read_data_dir_pointer() == target


def test_migrate_keeps_pointer_at_default_location(tmp_path) -> None:
    current = _point_at(tmp_path / paths_module.CONFIG_DIR_NAME)
    _seed(current)
    target = tmp_path / "elsewhere"

    migrate_data_dir(str(target))

    # 指针留在默认位置，不跟着搬 —— 否则下次启动就没有锚点了。
    assert not (target / paths_module.POINTER_FILE_NAME).exists()
    assert paths_module.data_dir_pointer_path().is_file()


def test_migrate_refuses_non_empty_target(tmp_path) -> None:
    current = _point_at(tmp_path / paths_module.CONFIG_DIR_NAME)
    _seed(current)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "important.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(DataDirOccupiedError):
        migrate_data_dir(str(target))

    # 失败后指针没动，数据还在原地，目标也没被碰。
    assert (current / "config.yaml").is_file()
    assert (target / "important.txt").read_text(encoding="utf-8") == "keep"
    assert not (target / "sessions").exists()


def test_migrate_on_missing_source_only_writes_pointer(tmp_path) -> None:
    _point_at(tmp_path / "never-created")
    target = tmp_path / "elsewhere"

    result = migrate_data_dir(str(target))

    assert result["moved"] == 0
    assert target.is_dir()
    assert paths_module.read_data_dir_pointer() == target


def test_migrate_into_empty_existing_dir(tmp_path) -> None:
    current = _point_at(tmp_path / paths_module.CONFIG_DIR_NAME)
    _seed(current)
    target = tmp_path / "elsewhere"
    target.mkdir()

    migrate_data_dir(str(target))

    assert (target / "config.yaml").is_file()


def test_suggested_data_dir_is_a_dedicated_folder() -> None:
    assert suggested_data_dir().name == "NonoAgent"


# ------------------------------------------------------------------
# 向上 walk：数据目录不能被当成「某个项目的配置」
# ------------------------------------------------------------------

def test_stale_default_dir_is_not_picked_up_as_project_config(tmp_path, monkeypatch) -> None:
    """数据目录搬走后，老位置的 config.yaml 不能被 walk 捡走。

    ``find_project_config`` 一路向上走到文件系统根。如果只按「是不是当前
    数据目录」判定，搬了家的用户就会重新读到老位置那份 config.yaml
    （可能还留着别的机器上配的模型和密钥）。
    """
    from nono_agent.providers import catalog as catalog_module

    # 家目录放在一个足够深的临时根下：walk 到顶也只经过 fake_home，
    # 不会真的爬到 C:\\Users\\Administrator 上（那台机器上有真实数据目录）。
    fake_home = tmp_path / "fakefs" / "Users" / "me"
    stale = fake_home / paths_module.CONFIG_DIR_NAME
    stale.mkdir(parents=True)
    (stale / "config.yaml").write_text(
        "agent_default_model: {provider: ghost, model: old}\n", encoding="utf-8"
    )
    moved = tmp_path / "moved-home"
    moved.mkdir()
    _point_at(moved)
    # 必须在解析之前打桩：``default_user_home()`` = Path.home()/.nono-agent。
    # 直接打桩 default_user_home 比 patch Path.home 稳。
    monkeypatch.setattr(
        paths_module, "default_user_home", lambda: fake_home / paths_module.CONFIG_DIR_NAME
    )

    assert paths_module.is_data_dir(stale)
    assert not paths_module.is_data_dir(moved / paths_module.CONFIG_DIR_NAME)
    # walk 会经过 stale 那层，但必须当作「用户数据目录」跳过 ——
    # 也就是不会把 stale 当成找到的项目配置。
    assert catalog_module.find_project_config(fake_home / "projects" / "foo") != stale


# ------------------------------------------------------------------
# 派生路径：重新解析之后，各处都从新目录长出来
# ------------------------------------------------------------------

def test_derived_paths_follow_pointer(tmp_path) -> None:
    """只断言**调用期**求值的函数。

    ``USER_CONFIG_PATH`` 这类是导入期常量，真实场景里搬迁本来就伴随一次
    重启；用 ``importlib.reload`` 去验反而会污染其它测试拿到的模块状态。
    """
    from nono_agent.session import transcript as transcript_module
    from nono_agent.settings import sources as settings_sources

    new = _point_at(tmp_path / "new-home")
    transcript_module.load_session_dir_setting()

    assert settings_sources.user_settings_path() == new / "settings.json"
    assert transcript_module.active_session_dir() == new / "sessions"


def test_app_info_reports_data_dir(tmp_path) -> None:
    from nono_agent.core.config import EngineConfig
    from nono_app_server.config import SidecarSettings
    from nono_app_server.engine import create_server_app
    from nono_app_server.features.router import mount_product_routes

    old = tmp_path / paths_module.CONFIG_DIR_NAME
    _seed(old)
    _point_at(old)
    new = tmp_path / "new-home"

    config = EngineConfig(cwd=str(tmp_path))
    app = create_server_app(config)
    mount_product_routes(
        app,
        SidecarSettings(
            host="127.0.0.1",
            port=8000,
            cwd=str(tmp_path),
            cors_origins=[],
            catalog_ready=False,
            catalog_error=None,
        ),
        config,
        db_path=tmp_path / "app.db",
        import_engine_index=False,
    )
    fastapi = pytest.importorskip("fastapi.testclient")
    with fastapi.TestClient(app) as client:
        before = client.get("/app/info").json()
        response = client.post("/app/data-dir", json={"path": str(new)})

    assert before["data_dir"] == str(old)
    assert before["data_dir_is_default"] is False  # 指针指向 old，已搬过家
    assert response.status_code == 200
    body = response.json()
    assert body["restart_required"] is True
    assert Path(body["data_dir"]) == new
    assert (new / "sessions" / "abc.jsonl.zst").read_bytes() == b"events"


def test_app_info_rejects_bad_target(tmp_path) -> None:
    from nono_agent.core.config import EngineConfig
    from nono_app_server.config import SidecarSettings
    from nono_app_server.engine import create_server_app
    from nono_app_server.features.router import mount_product_routes

    _point_at(paths_module.default_user_home())

    config = EngineConfig(cwd=str(tmp_path))
    app = create_server_app(config)
    mount_product_routes(
        app,
        SidecarSettings(
            host="127.0.0.1",
            port=8000,
            cwd=str(tmp_path),
            cors_origins=[],
            catalog_ready=False,
            catalog_error=None,
        ),
        config,
        db_path=tmp_path / "app.db",
        import_engine_index=False,
    )
    occupied = tmp_path / "busy"
    occupied.mkdir()
    (occupied / "x.txt").write_text("x", encoding="utf-8")

    fastapi = pytest.importorskip("fastapi.testclient")
    with fastapi.TestClient(app) as client:
        response = client.post("/app/data-dir", json={"path": str(occupied)})

    assert response.status_code == 409
    assert "空的" in response.json()["detail"]
