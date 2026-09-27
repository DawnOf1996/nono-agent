"""pytest 全局夹具 —— 隔离真实用户配置与会话目录，保证测试可重复。"""

from pathlib import Path

import pytest

from nono_agent.memory import store as memory_store_module
from nono_agent.providers import catalog as catalog_module
from nono_agent.session import transcript as transcript_module


@pytest.fixture(autouse=True)
def _isolate_user_home(tmp_path, monkeypatch):
    """把 ``user_home()`` 指到临时目录，避免测试读到真实 ``~/.nono-agent``。"""
    home = tmp_path / "isolated-user-nono-agent"
    home.mkdir()

    def _home() -> Path:
        return home

    monkeypatch.setattr("nono_agent.paths.user_home", _home)
    monkeypatch.setattr("nono_agent.settings.sources.user_home", _home)
    monkeypatch.setattr("nono_agent.mcp.launcher.user_home", _home)
    yield


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """把用户 LLM 目录与凭据路径指向临时目录，避免读到真实 ~/.nono-agent。"""
    root = tmp_path / ".nono-agent"
    monkeypatch.setattr(catalog_module, "USER_CONFIG_PATH", root / "config.yaml")
    monkeypatch.setattr(catalog_module, "USER_CREDENTIALS_PATH", root / "credentials")
    yield


@pytest.fixture(autouse=True)
def _isolate_session_dir(tmp_path, monkeypatch):
    """把默认会话目录指向临时目录 —— 引擎默认写事件流，不能落到真实 HOME。"""
    session_dir = tmp_path / ".nono-agent" / "sessions"
    monkeypatch.setattr(transcript_module, "DEFAULT_SESSION_DIR", session_dir)
    transcript_module.use_session_dir(None)
    yield
    transcript_module.use_session_dir(None)


@pytest.fixture(autouse=True)
def _isolate_memory_dir(tmp_path, monkeypatch):
    """把默认记忆目录指向临时目录 —— 引擎默认建 FileSystemMemoryStore。

    不隔离的话，记忆会在真实 HOME 里跨测试累积，检索类断言随运行历史漂移。
    """
    monkeypatch.setattr(
        memory_store_module, "DEFAULT_MEMORY_DIR", tmp_path / ".nono-agent" / "memory"
    )
    yield
