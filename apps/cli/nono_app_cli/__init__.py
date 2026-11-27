"""终端 CLI：消费 QueryEngine.submit_message() 并渲染到本地终端。"""

from nono_app_cli.app import create_cli_app, main
from nono_app_cli.config import build_engine_config, resolve_resume_bindings
from nono_app_cli.render import format_token_summary
from nono_app_cli.repl import run_cli

__version__ = "0.1.0"

__all__ = [
    "build_engine_config",
    "create_cli_app",
    "format_token_summary",
    "main",
    "resolve_resume_bindings",
    "run_cli",
]
