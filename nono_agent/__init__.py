"""Nono Agent —— Python AI Agent 引擎，灵感来源于 Claude Code 的 QueryEngine 架构。

提供核心组件的公共导出：
- QueryEngine: 顶层编排引擎
- EngineConfig/QueryConfig/ProviderConfig: 配置类型
- LLMProvider/ProviderRegistry: LLM Provider 抽象与注册
- TranscriptWriter/LocalFileTranscriptWriter: 会话事件流协议与默认实现
- SessionEvent: 会话事件信封（落盘只有事件）
- PermissionChecker: 工具权限检查器类型
- Tool: 工具协议基类
"""

from nono_agent.core.config import EngineConfig, ProviderConfig, QueryConfig  # 配置类型
from nono_agent.core.engine import QueryEngine  # 顶层编排引擎
from nono_agent.providers.base import LLMProvider, ProviderRegistry  # LLM Provider 抽象
from nono_agent.session.events import SessionEvent  # 会话事件信封
from nono_agent.session.transcript import (  # 会话事件流持久化
    LocalFileTranscriptWriter,
    TranscriptWriter,
)
from nono_agent.tools.context import PermissionChecker  # 权限检查器类型
from nono_agent.tools.protocol import Tool  # 工具协议基类

__all__ = [
    "QueryEngine",
    "EngineConfig",
    "QueryConfig",
    "ProviderConfig",
    "LLMProvider",
    "ProviderRegistry",
    "TranscriptWriter",
    "LocalFileTranscriptWriter",
    "SessionEvent",
    "PermissionChecker",
    "Tool",
]
