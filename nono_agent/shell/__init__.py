"""Shell 执行能力层。

工具不直接 spawn 进程：它描述一次执行（:mod:`~nono_agent.shell.types`），
由执行器完成。这条缝让未来的沙箱执行器能复用全部进程机制，只覆写 argv 构造。

模块分工：

- :mod:`~nono_agent.shell.dialect` —— 方言解析与 argv 构造
- :mod:`~nono_agent.shell.preamble` —— 编码声明与退出码归一化
- :mod:`~nono_agent.shell.spill` —— 字节环形缓冲与溢出落盘
- :mod:`~nono_agent.shell.streams` —— stdout/stderr 分离收集
- :mod:`~nono_agent.shell.types` —— 执行协议
- :mod:`~nono_agent.shell.local` —— 本地执行器
- :mod:`~nono_agent.shell.jobs` —— 会话级作业注册表
"""

from __future__ import annotations

from nono_agent.shell.dialect import (
    DIALECT_NAMES,
    ShellDialect,
    build_argv,
    env_overrides,
    resolve_dialect,
)
from nono_agent.shell.spill import OutputDelta, SpillWriter
from nono_agent.shell.streams import StreamCollector, StreamPair

__all__ = [
    "DIALECT_NAMES",
    "OutputDelta",
    "ShellDialect",
    "SpillWriter",
    "StreamCollector",
    "StreamPair",
    "build_argv",
    "env_overrides",
    "resolve_dialect",
]
