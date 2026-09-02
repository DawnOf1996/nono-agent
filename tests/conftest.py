"""pytest 全局夹具 —— 隔离真实用户配置与会话目录，保证测试可重复。"""

import pytest

from nono_agent.adapters import onboarding as ob
from nono_agent.core import engine as engine_module
from nono_agent.memory import store as memory_store_module
from nono_agent.session import transcript as transcript_module


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """把用户基础配置路径指向临时目录，避免测试读到 ~/.nono-agent/config.json。"""
    monkeypatch.setattr(ob, "USER_CONFIG_PATH", tmp_path / ".nono-agent" / "config.json")
    yield


@pytest.fixture(autouse=True)
def _isolate_session_dir(tmp_path, monkeypatch):
    """把默认会话目录指向临时目录 —— 引擎默认写事件流，不能落到真实 HOME。"""
    session_dir = tmp_path / ".nono-agent" / "sessions"
    monkeypatch.setattr(engine_module, "DEFAULT_SESSION_DIR", session_dir)
    monkeypatch.setattr(transcript_module, "DEFAULT_SESSION_DIR", session_dir)
    yield


@pytest.fixture(autouse=True)
def _isolate_memory_dir(tmp_path, monkeypatch):
    """把默认记忆目录指向临时目录 —— 引擎默认建 FileSystemMemoryStore。

    不隔离的话，记忆会在真实 HOME 里跨测试累积，检索类断言随运行历史漂移。
    """
    monkeypatch.setattr(
        memory_store_module, "DEFAULT_MEMORY_DIR", tmp_path / ".nono-agent" / "memory"
    )
    yield
