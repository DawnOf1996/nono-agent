---
title: 新增工具插件
---

# 新增工具插件

工具插件是**声明式**的：一个装着 `plugin.json` 的目录就是全部。引擎侧只有框架
（`nono_agent/toolplugins/`），不含任何具体厂商集成；插件属于**产品定义**，随产品包发布。

!!! info "什么时候用插件而不是内置工具"
    工具插件适合：需要 API 密钥、需要用户开关、调的是外部 HTTP 端点、或者要桥接一个 MCP server。
    这些都不该进内置工具表 —— 内置只保留零配置即用的原语。

---

## 1. 清单格式

一个插件 = 一个目录 + 一个 `plugin.json`。两种工具声明：`http_json`（一个 HTTP JSON 端点）和 `mcp`（桥接本插件 `mcp` 段里声明的 server）。

```json
{
  "name": "acme-search",
  "version": "0.1.0",
  "description": "Acme 网页搜索。",
  "config": {
    "api_key": {
      "label": "Acme API Key",
      "hint": "在 acme.dev 创建，保存在本机凭据文件里。",
      "placeholder": "acme-..."
    },
    "fields": [
      { "key": "model", "label": "模型", "type": "string", "hint": "默认 small" },
      { "key": "base_url", "label": "API 地址", "type": "string" },
      { "key": "server.transport", "label": "连接方式", "type": "enum",
        "choices": [{ "value": "http", "label": "远程 HTTP（推荐）" },
                    { "value": "stdio", "label": "本地 npx" }] }
    ]
  },
  "tools": {
    "search": {
      "kind": "http_json",
      "prefix": "acme",
      "description": "Search the web via Acme.",
      "input_schema": {
        "type": "object",
        "properties": { "query": { "type": "string", "description": "Search query" } },
        "required": ["query"]
      },
      "method": "POST",
      "url": "https://api.acme.dev/v1/search",
      "headers": { "Content-Type": "application/json" },
      "body": { "q": "${args.query}", "model": "${options.model}" },
      "timeout_s": 60,
      "result": { "kind": "text" },
      "is_concurrent_safe": true,
      "has_write_side_effects": false,
      "auth": { "type": "bearer", "api_key_env": "NONO_ACME_API_KEY" },
      "defaults": { "model": "small" }
    }
  }
}
```

最终工具名是 `{prefix}__{name}`，即 `acme__search`。

### `config` 段（设置页渲染用）

**前端不认识任何具体插件** —— 能配什么、渲染成什么控件，全由清单自己的 `config` 段决定。

- 控件类型：`string` / `number` / `boolean` / `enum` / `password`。
- **默认值不写在 schema 里**：现值按 `settings.tools.<id>.options` ← 工具声明的 `defaults` 回落，两处不各写一份。
- `key` 支持点分路径：`server.transport` 落到 `options["server"]["transport"]`。MCP 的连接字段天生嵌套，写扁平键会存进去但运行时读不到。
- 密钥只有「往哪个凭据名里填」这一个指针。读接口只回答 `configured`，**永不回显密钥**。

### 模板变量

`${args.<name>}` 取模型传进来的参数（**缺省时整个字段丢弃，不送 `null`**），`${options.<key>}` 取用户配置。

### 密钥注入

| `auth.type` | 注入位置 | 何时用 |
|---|---|---|
| `none`（默认） | 不注入 | 无需密钥的工具；省略整个 `auth` 段就是这个 |
| `bearer` | `Authorization: Bearer <key>` | 绝大多数服务，**要显式写 `"type": "bearer"`** |
| `header` | `header` 指定的头，如 `X-Api-Key` | 厂商自定义头 |
| `query` | `?<query_param>=<key>` | 兜底：服务把 Key 设计在 URL 上 |

```json
"auth": { "type": "query", "api_key_env": "XXX_API_KEY", "query_param": "apiKey" }
```

选 `query` 时框架会做两件补偿：按 URL 规则转义（否则带 `&`/`+`/`=` 的 Key 会把查询串截断），以及在任何对外错误文本里抹掉它（含转义形式）。优先用 `bearer` / `header` —— URL 会进访问日志与浏览器历史。

### 两种映射器

- **`result: {"kind": "save_images"}`** —— 响应侧，把 base64 / URL 两种返回都落盘到 `output_dir` 并给 UI 附图片卡片。工具结果里塞图对当前 provider 链路没意义（只有 user 消息会被解析成 image 块）。
- **`inputs: {"kind": "image_data_urls"}`** —— 请求侧，把**本地文件**读成 data URL。图生图这类接口要公网 URL 或 base64，而模型手里只有工作区路径，这一层就是那座桥。它是声明式的替代方案：模板层刻意不提供 `${file:...}`，否则任意 `plugin.json` 都能变成任意文件读。读取一律过沙箱，按**魔数**（不按扩展名）校验 JPG/PNG，并卡体积上限。

### `mcp` 声明

```json
{
  "mcp": {
    "acme": { "type": "http", "url": "https://mcp.acme.dev/mcp/" }
  },
  "tools": {
    "bridge": {
      "kind": "mcp",
      "server": "acme",
      "allowed_tools": ["acme_search", "acme_extract"],
      "auth": { "type": "bearer", "api_key_env": "NONO_ACME_API_KEY" },
      "defaults": { "server": { "transport": "http" } }
    }
  }
}
```

工具名是 `mcp__<server>__<name>`。`allowed_tools` 为空表示全部暴露。用户/项目已在 `mcp.json` 里配了**同名** server 时，内置预设自动让位（插件自己 `mcp` 段里声明的除外）。

---

## 2. 放到哪里

| 位置 | 语义 |
|---|---|
| `apps/server/nono_app_server/features/toolplugins/plugins/<name>/plugin.json` | 产品内置，随产品发布 |
| `.nono-agent/plugins/<name>/plugin.json` | 工作区级，同名覆盖内置 |
| `~/.nono-agent/plugins/<name>/plugin.json` | 用户级 |
| 自建目录 | 在自己的产品入口 `register_plugin_dir(Path(...))` 挂上 |

产品侧 `create_app()` 会调一次 `register_builtin_plugins()`。注册顺序即优先级：先注册的在前，后注册的同名插件覆盖先注册的；用户级与项目级仍排在所有注册目录之后。

!!! warning "CLI 拿不到内置插件"
    终端版直接用引擎、不依赖 `nono-app-server`，所以看不到上面那两个内置插件。要让 CLI 也有，把插件目录挪到双方都依赖的包里，或让 CLI 也依赖 `nono-app-server` 并调一次 `register_builtin_plugins()`。

---

## 3. 启用与密钥

**未声明即不启用。** 在工作区 `.nono-agent/settings.json`（或用户级 `~/.nono-agent/settings.json`）里显式开：

```json
{
  "tools": {
    "acme-search": {
      "enabled": true,
      "api_key_env": "NONO_ACME_API_KEY",
      "options": {
        "model": "small",
        "base_url": "https://api.acme.dev",
        "server": { "transport": "http" }
      }
    }
  }
}
```

**密钥不写 settings.json**：值存进 `~/.nono-agent/credentials`（`NONO_ACME_API_KEY=...`）或同名环境变量，配置里只留 `api_key_env` 指针。

HTTP 端点（`options.base_url`）可改，因此每次请求都过 SSRF 判定；错误信息里的密钥一律脱敏。

会话内改开关不用重启：引擎层 `engine.reload_tool_plugins()`，产品层 `POST /app/tool-plugins/reload`。活跃会话会热重载。

---

## 4. 测试

```python
# tests/test_tool_plugins.py
import json
from pathlib import Path

import pytest

from nono_agent.toolplugins.registry import register_plugin_dir, clear_plugin_dirs
from nono_agent.toolplugins.spec import parse_tool_specs, ToolSpecError


@pytest.fixture
def plugin_dir(tmp_path):
    clear_plugin_dirs()
    d = tmp_path / "acme-search"
    d.mkdir()
    (d / "plugin.json").write_text(json.dumps({
        "name": "acme-search",
        "tools": {"search": {
            "kind": "http_json",
            "prefix": "acme",
            "url": "https://api.acme.dev/v1/search",
            "input_schema": {"type": "object", "properties": {}},
        }},
    }), encoding="utf-8")
    yield d
    clear_plugin_dirs()


def test_tool_name_is_prefixed(plugin_dir):
    register_plugin_dir(plugin_dir.parent)          # 注册的是「发现根目录」，插件在其子目录里
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    specs = parse_tool_specs("acme-search", manifest["tools"])
    assert [s.tool_name for s in specs] == ["acme__search"]


def test_name_falls_back_to_plugin_id(plugin_dir):
    """不写 prefix 时，前缀退回插件 id（`-` 转 `_`）。"""
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    manifest["tools"]["search"].pop("prefix")
    specs = parse_tool_specs("acme-search", manifest["tools"])
    assert [s.tool_name for s in specs] == ["acme_search__search"]


def test_http_json_requires_url(tmp_path):
    with pytest.raises(ToolSpecError):
        parse_tool_specs("bad", {"t": {"kind": "http_json"}})
```

要覆盖更多解析分支、密钥注入与脱敏，看已有的 `tests/test_tool_plugins.py` 与 `tests/test_tool_plugin_auth.py`；框架细节见 [工具插件框架源码注释](https://gitee.com/dawn1996/nono-agent)。

---

## 5. 检查清单

- [ ] `plugin.json` 放对目录（内置 / 项目 / 用户 / 自建）
- [ ] 两种 `kind` 之一：`http_json` 或 `mcp`
- [ ] 密钥只写 `api_key_env`，明文进 `~/.nono-agent/credentials`
- [ ] `config.fields` 的 `key` 用点分路径表达嵌套
- [ ] `defaults` 写在工具声明里，不要在 `config.fields` 里重复
- [ ] 若有产品侧插件，在 `create_app()` / `register_builtin_plugins()` 挂上
- [ ] 补 `tests/test_tool_plugins.py` 用例
- [ ] 在 [使用手册 §3.5](../guide/usage.md) 登记这个插件

---

## 相关页面

- [新增内置工具](add-builtin-tool.md)
- [配置与密钥](../getting-started/configuration.md)
- [使用手册：工具与权限](../guide/tools-and-permissions.md)
