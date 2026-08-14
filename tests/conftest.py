"""pytest 全局夹具 —— 隔离真实用户配置，保证测试可重复。"""

import pytest

from nono_agent.adapters import onboarding as ob


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """把用户基础配置路径指向临时目录，避免测试读到 ~/.nono-agent/config.json。"""
    monkeypatch.setattr(ob, "USER_CONFIG_PATH", tmp_path / ".nono-agent" / "config.json")
    yield
