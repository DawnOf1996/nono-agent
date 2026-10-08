"""pytest 全局夹具 —— 隔离真实用户数据目录，保证测试可重复。

数据目录走 ``NONO_DATA_DIR`` 环境变量隔离，而不是 monkeypatch ``user_home``：
各模块（catalog / transcript / memory / settings.sources …）都是在**导入期**
把 ``user_home()`` 的结果存成模块常量，事后替换函数绑定并不会改已经算好的常量。
环境变量则能被 :func:`nono_agent.paths.user_home` 自己解析到，所有调用方
（包括直接 ``from ... import user_home`` 的那些）自动一起隔离。
"""

from pathlib import Path

import pytest

from nono_agent import paths as paths_module
from nono_agent.memory import store as memory_store_module
from nono_agent.providers import catalog as catalog_module
from nono_agent.session import transcript as transcript_module


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """把数据目录指到临时目录，避免测试读到真实 ``~/.nono-agent``。"""
    home = tmp_path / "isolated-user-nono-agent"
    home.mkdir()
    monkeypatch.setenv(paths_module.ENV_DATA_DIR, str(home))
    paths_module.reset_user_home_cache()
    yield home
    paths_module.reset_user_home_cache()


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """把用户 LLM 目录与凭据路径指向临时目录，避免读到真实 ~/.nono-agent。

    这两个是导入期常量（``USER_CONFIG_PATH`` 等），环境变量管不到，只能改符号。
    """
    root = tmp_path / "user-config"
    monkeypatch.setattr(catalog_module, "USER_CONFIG_PATH", root / "config.yaml")
    monkeypatch.setattr(catalog_module, "USER_CREDENTIALS_PATH", root / "credentials")
    yield


@pytest.fixture(autouse=True)
def _isolate_session_dir(tmp_path, monkeypatch):
    """把默认会话目录指向临时目录 —— 引擎默认写事件流，不能落到真实数据目录。

    ``load_session_dir_setting()`` 会在启动时重新按数据目录推导这个值，
    所以这里固定住它，防止某个测试真的去调那个函数时改到全局状态。
    """
    session_dir = tmp_path / "user-config" / "sessions"
    monkeypatch.setattr(transcript_module, "DEFAULT_SESSION_DIR", session_dir)
    transcript_module.use_session_dir(None)
    yield
    transcript_module.use_session_dir(None)


@pytest.fixture(autouse=True)
def _isolate_memory_dir(tmp_path, monkeypatch):
    """把默认记忆目录指向临时目录 —— 引擎默认建 FileSystemMemoryStore。

    不隔离的话，记忆会在真实目录里跨测试累积，检索类断言随运行历史漂移。
    """
    monkeypatch.setattr(
        memory_store_module, "DEFAULT_MEMORY_DIR", tmp_path / "user-config" / "memory"
    )
    yield


@pytest.fixture
def data_dir_home() -> Path:
    """当前测试的数据目录 —— 需要在里面放文件时用它。"""
    return paths_module.user_home()
