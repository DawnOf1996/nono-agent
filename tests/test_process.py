"""Windows 子进程隐藏控制台窗口。"""

import sys

from nono_agent.utils.process import hidden_kwargs


def test_hidden_kwargs_platform() -> None:
    kwargs = hidden_kwargs()
    if sys.platform == "win32":
        import subprocess

        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    else:
        assert kwargs == {}
