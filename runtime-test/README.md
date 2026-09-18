# runtime-test：验证 nono-agent 调用 MCP

把本目录当作 `--cwd`。`verify_mcp.py` 会直接读本目录 `.nono-agent/mcp.json`。交互式 `nono chat --cwd runtime-test` 从用户级 `~/.nono-agent/mcp.json` 读连接清单（可把本目录那份拷过去）。启停在工作区 `.nono-agent/settings.json` 的 `mcp.servers.<id>`；默认不启用，本目录已打开 `calculator` / `username`。


| server       | 传输              | 工具                                         | 说明                             |
| ------------ | --------------- | ------------------------------------------ | ------------------------------ |
| `calculator` | stdio           | `add` / `subtract` / `multiply` / `divide` | CLI 启动时按 `command`/`args` 原样拉起 |
| `username`   | Streamable HTTP | `get_user_name`                            | 需先单独起进程；校验 Bearer + 自定义头       |


HTTP 认证约定（写在 `mcp.json` 的 `headers` 里，对齐 Cursor）：

- `Authorization: Bearer runtime-test-secret`
- `X-Demo-Auth: header-ok`
- `X-Demo-User: Alice`（工具返回这个名字，用来确认自定义头被带上）

按 server 关闭或隐藏工具时改 `settings.json`，例如 `"disabledTools": ["divide"]`，不要往 `mcp.json` 加 `enabled` / `token`。

## 1. 先起 HTTP MCP

在仓库根目录：

```bash
uv run python runtime-test/mcp_servers/username_http.py
```

健康检查：`http://127.0.0.1:8889/health`

## 2. 再开 nono-agent

另开一个终端，仓库根目录：

```bash
uv run nono chat --cwd runtime-test
```

进 REPL 后：

1. `/mcp` 应看到 `calculator` running、`username` running
2. `/permission bypass`（MCP 工具带写副作用，默认模式会问权限）
3. 让模型做一次加减法，或问「当前用户叫什么」

桥接后的工具名：

- `mcp__calculator__add`
- `mcp__username__get_user_name`



## 3. 不走对话的冒烟

同样先起 HTTP 服务，再：

```bash
uv run python runtime-test/verify_mcp.py
```

