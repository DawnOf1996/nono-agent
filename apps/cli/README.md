# apps/cli

终端产品入口。不复制引擎，只组合 `QueryEngine.submit_message()`，用 Typer 解析参数、Rich 渲染输出。

```text
nono_app_cli/
  app.py          Typer 入口（chat / sessions）
  config.py       YAML 目录 → EngineConfig
  catalog.py      首次运行引导 / 补密钥
  onboarding.py   交互收集供应商目录
  repl.py         交互循环与单轮处理
  render.py       SDK 流渲染
  interactive.py  权限确认 / AskUser / MCP 反馈
```

## 运行

仓库根已 `uv sync --extra dev` 时：

```bash
uv run nono chat
```

或只装本包：

```bash
cd apps/cli
uv sync
uv run nono chat
```

常用命令：

```bash
uv run nono chat
uv run nono chat -m "你好" --model MiniMax-M3 --provider a
uv run nono chat --log-api-requests
uv run nono sessions
```

首次交互式运行会引导写入 `~/.nono-agent/config.yaml`。环境变量：`NONO_MODEL`、`NONO_CWD`、`NONO_LOG_API_REQUESTS`。
