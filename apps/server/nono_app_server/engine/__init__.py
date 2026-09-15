"""引擎 HTTP API：会话管理 + SSE。产品层在 `nono_app_server.product` 挂 /app 路由。"""

from nono_agent.session.transcript import DEFAULT_SESSION_DIR
from nono_app_server.engine.app import create_server_app, run_server

__all__ = ["DEFAULT_SESSION_DIR", "create_server_app", "run_server"]
