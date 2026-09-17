"""隐藏 Windows 控制台窗口的子进程参数。

打包后的桌面 sidecar 是 windowed 进程。在 Windows 上再拉起 git / cmd
等 console 子系统程序时，系统会弹出一个黑框。``CREATE_NO_WINDOW``
可以避免这一点；其他平台返回空字典。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def hidden_kwargs() -> dict[str, Any]:
    """给 ``subprocess.run`` / ``asyncio.create_subprocess_*`` 展开使用。"""
    if sys.platform != "win32":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}
