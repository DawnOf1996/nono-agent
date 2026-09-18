"""Settings 分层配置 —— default / user / project / policy / flag 合并。

统一读入口：各子系统通过合并后的 Settings 读取，不各自读文件。
"""

from nono_agent.settings.models import Settings
from nono_agent.settings.sources import (
    DEFAULT_SETTINGS,
    load_project_settings,
    load_settings,
    load_settings_from_env,
    load_user_settings,
    patch_project_settings,
    project_settings_path,
)

__all__ = [
    "DEFAULT_SETTINGS",
    "Settings",
    "load_project_settings",
    "load_settings",
    "load_settings_from_env",
    "load_user_settings",
    "patch_project_settings",
    "project_settings_path",
]
