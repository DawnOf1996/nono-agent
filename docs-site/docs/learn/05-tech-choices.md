---
title: 05 · 技术选型
---

# 技术选型

> 回答一个问题：**为什么是这个技术，而不是别的？**
> 每一条都给出替代方案和取舍，不只说好处。

---

## 1. 语言与运行时

### Python 3.12+

| 决策 | 为什么 | 代价 |
|------|--------|------|
| 选 Python | Agent 生态、AI 库、团队上手成本都在这边 | 性能上限低于 Rust/Go |
| 要求 3.12+ | 用 `type` 参数语法、`asyncio.TaskGroup`、更快的解释器 | 放弃了更老的 Python |

> 一个已知的小瑕疵：`pyproject.toml` 里 mypy 的 `python_version` 还写着 `"3.11"`，
> 而运行时和 CI 都是 3.12。也就是**类型检查按 3.11 语义跑**。改它可能一次冒出一批错误，
> 所以单独处理，别混在别的 PR 里。

---

## 2. 类型与数据建模

这里有一个**刻意的分层**，值得单独讲：

| 场景 | 用什么 | 例子 |
|------|--------|------|
| 消息与内容块 | **Pydantic v2 `BaseModel`** | `core/types.py` 全部 27 个消息/块类（`ToolUseBlock`、`UserMessage`、`SDKResultMessage`…） |
| 事件信封 | **Pydantic `BaseModel`** | `SessionEvent`（`session/events.py`） |
| 需要校验的边界配置 | **Pydantic `BaseModel` + `field_validator`** | `EngineConfig`、`ShellConfig`、`ProviderConfig`、模型目录条目 |
| 不可变循环状态 | **`dataclass(frozen=True)`** | `LoopState`、`Transition`、`Terminal`、`AutoCompactTracking` |
| 无校验的领域模型 | **标准库 `dataclass`** | `MemoryRecord`、`Rule`、`PermissionRule` |
| 可插拔契约 | **`Protocol` + `@runtime_checkable`** | `Tool`、`LLMProvider`、`MemoryStore`……（`MetricsRecorder` 是唯一没加该装饰器的） |
| 需要写盘成 JSON | **手写 `to_dict`/`from_dict`** | `PermissionRule`、`PluginManifest` |

**规律：凡是"要序列化成协议、要在构造时校验"的，都用 Pydantic；
凡是"只在进程内传、逻辑上不可变"的，用 frozen dataclass。**

全项目只有 4 个文件 import pydantic：`core/types.py`、`core/config.py`、
`session/events.py`、`providers/catalog.py`。

**为什么状态机用 dataclass 而不是 Pydantic？**
`LoopState` 每轮循环要构造新实例，而且它需要的是**不可变性**而非校验。
`frozen=True` 一行就拿到不可变，而且不需要为了几个字段引入一整套框架依赖。

> 注意 `frozen=True` 的边界：它防的是**就地改**，不防**漏传字段**——
> 漏传会静默取默认值。详见 [06 · 设计取舍](06-design-decisions.md)。

---

## 3. 协议 vs 继承：全项目的主导模式

几乎每个可插拔点都是 `Protocol`：

```python
@runtime_checkable
class Tool(Protocol):
    @property
    def name(self) -> str: ...
    async def execute(self, input_params, context) -> ToolResult: ...
```

**为什么用 Protocol 而不是 ABC？**

- **结构化子类型**：不需要 `class MyTool(Tool)`，只要方法齐了就算数。新人不必记继承树。
- **跨包零耦合**：`tools` 包不需要知道 `toolplugins` 包的存在；后者构造一个实现了同名方法的
  对象就能注册进 `ToolRegistry`。
- **可组合**：一个类可以同时是 `Tool` 和 `ShellExecutor`。

**代价**：结构化子类型没有编译期保证——方法名拼错可能到运行时才炸。
`@runtime_checkable` 只能检查方法**存在**，不能检查签名。
所以本项目的做法是"Protocol 定契约 + `XxxBase` 基类给便利默认"，
新人写工具时继承 `ToolBase` 就自动拿到正确的方法签名和 `to_api_schema()`。

---

## 4. 异步模型

| 决策 | 替代方案 | 为什么选它 |
|------|---------|-----------|
| `AsyncGenerator` 做对外 API | 回调 / 事件总线 | 流向明确、生命周期可控、取消天然（关生成器即取消） |
| `asyncio.create_subprocess_exec` | `subprocess` + 线程 | 免阻塞事件循环，避免大量线程 |
| `AsyncExitStack` 管 MCP 客户端 | 手写 try/finally | 多资源（进程、连接、客户端）按序清理 |
| `asyncio.to_thread` 桥同步 | 强行改造成异步 | 遇到不能改的同步库时的务实退路 |
| `threading.Lock` 保护 metrics | `asyncio.Lock` | metrics 也可能被同步代码调用 |

### 一个值得学的性能细节

`mcp/__init__.py` 用了 **PEP 562 惰性导入**。原因写在 docstring 里：

> 单是 sidecar 启动链上一个 `from nono_agent.mcp.X import ...` 就会把整个 MCP SDK 拖进来，
> 实测约 630ms，占 sidecar 启动近三成。

解法是包级 `__getattr__` + 一个 `_EXPORTS` 映射表，首次访问才导入并缓存进 `globals()`。
**这是"避免昂贵 import"的教科书写法**：Python 会先执行包的 `__init__`，所以 `__init__` 必须轻。

---

## 5. 网络与外部调用

| 决策 | 为什么 |
|------|--------|
| `httpx` | 原生 async，比 `requests` 快且能和 asyncio 融合 |
| OpenAI / Anthropic 官方 SDK | 各自协议形态复杂（尤其 `anthropic_messages` 的 thinking、工具 schema），自己重写不划算 |
| `openai.AsyncOpenAI` 同时服务两种协议 | `openai_chat_completions` 与 `openai_responses` 都走它 |

### 为什么 Provider 按"协议"而不是"厂商"分类

现实里很多厂商只是**兼容某一种 API 形态**。按协议分类后，
接一个新供应商通常**零代码**——填 `api_key` + `base_url` + 选个 `adapter` 就行。
只有遇到全新 API 形态（比如 `anthropic_messages`）才需要写适配器。

代价是：如果某厂商同时提供两种形态，你得知道该选哪个。所以 `adapter` **必须显式指定**，
未指定时只匹配显式注册的前缀，**没有隐式回退**——宁可报错也不猜错。

---

## 6. 存储

| 场景 | 选择 | 为什么 |
|------|------|--------|
| 默认会话日志 | **本地文件 + JSONL + zstd** | 零配置、append-only 天然可续写；zstd 独立帧可增量追加 |
| 备选会话后端 | **SQLite** | 需要按类型/步骤查询聚合时才换 |
| 配置 | **JSON / YAML + 分层合并** | 人可读、可手改 |
| 跨会话记忆 | **一记录一 JSON 文件**（`memdir`） | 简单、可直接 `cat` 出来看 |

**为什么不用数据库做默认？** 零配置 + 无需进程是这个项目的核心价值。
SQLite 后端存在，但要用户自己构造 `SQLiteTranscriptWriter` 传进引擎。

**为什么用 zstd 而不是 gzip？**
gzip 是流式但**不能随机续写独立帧**；zstd 独立帧可增量追加，
`resume` 时可以直接往后写而不必重压整个文件。旧 `.jsonl` 在首次追加时自动迁移成 `.jsonl.zst`。

---

## 7. 权限与安全

| 决策 | 为什么 |
|------|--------|
| `fnmatch` 做 glob | 标准库够用，够表达"工作区内/外" |
| 正则做 bash 模式匹配 | 危险命令分类需要子串级判断 |
| 默认 fail-closed | 见下 |
| SSRF 闸门 `net_guard.py` | `web_fetch` 和工具插件 HTTP 执行器共用一份实现 |
| 事件流按 `0600`/目录 `0700` 落盘 | 事件流逐字含提示词和密钥 |

### fail-closed 是一整套连贯的选择

- 工具默认 `is_concurrent_safe=False`（忘声明→串行，慢但不竞争）
- 工具默认 `has_write_side_effects=True`（忘声明→要确认）
- 权限默认 `default`（写操作先问）
- `bypass` 模式下**显式 `DENY` 规则依然生效**
- 非法配置往安全方向降级（非法 mode → `default`，不是放行）

一句话：**任何"没想清楚"的地方，都倒向更安全的那一边。**

---

## 8. 可观测性

| 决策 | 为什么 |
|------|--------|
| `structlog` | 结构化日志，Server 可 JSON、CLI 可彩色 |
| 自建 `MetricsRecorder` 协议 | 默认内存实现，测试可断言；生产可换 OTel/Prometheus |
| 事件流投影成指标 | 指标是事件流的聚合投影，两者主键天然对齐（`session_id`） |

**为什么指标不自己埋点，而是从事件流投影？**
这样只有一条真源：事件流记了的事实，指标自动跟着有。
不会出现"日志里有、指标里没有"这种对不上的情况。详见
[会话可观测性](../architecture/session-observability.md)。

**为什么 TokenTracker 不记钱（USD）？**
docstring 明说"不再追踪价格"。价格随模型和折扣变动，维护一张价格表是持续负担，
而"用了多少 token、哪个模型用的"才是你要做决策的信息。

---

## 9. 前端与产品层

| 层 | 技术 |
|----|------|
| CLI | Typer（命令行）+ Rich（终端渲染） |
| Server | FastAPI + SSE |
| Web | React + Vite |
| Desktop | Electron |
| 前后端共享类型 | 独立 TS 包 `packages/protocol` |

**为什么 Server 推 SSE 而不是 WebSocket？**
SSE 基于 HTTP，自带断线重连语义，服务端实现简单（FastAPI 一行 `StreamingResponse`），
且是单向推送——本项目的需求就是服务端→客户端推事件流，客户端→服务端用普通 HTTP POST。
WebSocket 作为规划能力留在路线图里。

---

## 10. 工具插件：为什么是声明式

`toolplugins/` 让"开开关 + 填 API Key"就变成一个可用工具。关键设计是：

> **插件清单不含可执行代码路径**，引擎因此不需要给插件开 Python 导入的口子。

整个插件就是一个 `plugin.json` + 少量资源文件。引擎只认两种 `kind`：
`http_json`（一个 HTTP JSON 端点）和 `mcp`（启用一个 MCP server）。
没有 `import`，意味着插件不可能在引擎里执行任意代码——**这既是安全边界，也是分发优势**
（丢个目录就能用，不用 `pip install`）。

完整规范见 [使用手册](../guide/usage.md)。

---

## 11. 继续

- 每个设计决策背后的取舍 → [06 · 设计取舍](06-design-decisions.md)
- 想动手改代码 → [开发指南](../development/index.md)