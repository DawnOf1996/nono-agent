# Nono Agent 上下文压缩策略设计

> 本文描述 `nono_agent` 的上下文压缩机制，并记录 2026-02 一轮整改（依据本文旧版梳理出的
> 问题清单）落地后的现状：
>
> 1. **基于模型上下文大小压缩？** —— 是。各压缩 stage 按配置窗口的百分比
>    （50% / 60% / 75% / 80% / 95%）触发，窗口来自模型配置 `model_context`。
> 2. **上下文大小在哪里配置？** —— 在**模型配置**里：`ProviderConfig.model_context`
>    （优先）> `EngineConfig.model_context`（兜底）> `DEFAULT_CONTEXT_WINDOW`（200k，最后防线）。
>    程序**不再基于模型 ID 前缀查表猜测**，原 `MODEL_CONTEXT_WINDOWS` 表已移除。
> 3. **system + 工具计入总上下文？** —— 是。管线估算与阈值判断均包含系统提示词与
>    工具 schema 的固定开销（`PipelineContext.overhead_tokens`）。
> 4. **上下文使用分析？** —— `QueryEngine.analyze_context_usage()`（以及
>    `utils.tokens.analyze_context_usage()`）返回总量、占比与系统提示词/工具/对话消息
>    三个分项的 token 明细；`/context` 命令直接展示该分析。

---

## 1. 压缩体系总览

Nono Agent 的上下文控制分四个入口，按发生时机排列：

| 入口 | 时机 | 实现位置 | 窗口感知 |
| --- | --- | --- | --- |
| ① 主动预处理管线 | 每次 LLM 调用前（`Phase 1`） | `pipeline/`（5 个 stage） | ✅ 按配置窗口百分比触发 |
| ② 反应式恢复链 | API 报错之后（`prompt_too_long` 等） | `recovery/` + `core/loop.py` | ❌ 固定 token 数 |
| ③ 手动压缩 | 用户 `/compact` | `core/engine.py::compact()` | ❌ 按消息条数 |
| ④ 记忆注入预算 | 组装 system prompt 时 | `memory/manager.py` | ❌ 固定 token 预算 |

执行路径：`core/engine.py` 构造引擎时创建默认管线（`_create_default_pipeline`）并传入
`agentic_loop`；循环每个 step 的 `Phase 1` 构造 `PipelineContext` 并执行管线
（loop.py），失败后进入 `RecoveryChain` 恢复。子 Agent（`agents/runner.py`）自建
`QueryEngine`，同样获得默认管线。

---

## 2. Token 估算方式（压缩的度量基准）

`nono_agent/utils/tokens.py`：

- `count_tokens()`：优先用 **tiktoken `cl100k_base`** 精确计数，缺库时回退 `len(text) // 4` 粗估。
- `estimate_message_tokens()`：单条消息 = 4（消息开销）+ role + 各 content block（text / 工具调用 input JSON / 工具结果 content）。
- `estimate_system_prompt_tokens()` / `estimate_tools_tokens()`：系统提示词与工具
  schema 的分项估算（工具按整体 JSON 序列化计数 + 单工具固定开销 8）。
- `estimate_request_tokens()`：`system + tools + messages` 三项合计。
- `analyze_context_usage()`：见 §6。

**总上下文口径（本次整改关键点）**：循环构造 `PipelineContext` 时

```python
overhead_tokens = estimate_system_prompt_tokens(system_prompt) + estimate_tools_tokens(api_tools)
estimated_tokens = overhead_tokens + estimate_messages_tokens(messages)
```

即 **system prompt 与工具 schema 计入总上下文**，压缩阈值按「全量请求体积」判断；
两部分作为固定开销记录在 `PipelineContext.overhead_tokens`（不随消息压缩变化），
stage 重新估算消息部分后需加回（`AutoCompactStage._reestimate` / SnipCompact 的
`total + context.overhead_tokens`）。

---

## 3. 模型上下文大小的配置与解析

**配置入口（本次整改新增）**：

| 位置 | 字段 | 说明 |
| --- | --- | --- |
| `ProviderConfig.model_context` | `int \| None` | 模型级配置，**最高优先**（如 `ProviderConfig(model_id="MiniMax-M3", model_context=196_608)`） |
| `EngineConfig.model_context` | `int \| None` | 引擎级兜底（provider 未指定时生效） |
| `utils/tokens.py DEFAULT_CONTEXT_WINDOW` | `200_000` | 两级配置都缺省时的最后防线 |

解析：`EngineConfig.effective_model_context` ——
`provider.model_context` → `model_context` → `DEFAULT_CONTEXT_WINDOW`（负数/0 视为未配置，
见 `resolve_context_window()`）。

**CLI/文件配置链路**（`apps/cli/nono_app_cli/config.py::build_engine_config`）已接入该字段：
`~/.nono-agent/config.json` 或 `--config` 文件中的 `provider.model_context`（或顶层
`model_context`），环境变量 `NONO_MODEL_CONTEXT` 优先级最高（env > provider 段 > 顶层）。
注意模型配置走的是 `config.json` 链路；`.nono-agent/settings.json` 属于 Settings 体系
（权限等），与模型上下文无关。

**已移除**：`MODEL_CONTEXT_WINDOWS` 前缀表与 `get_context_window_size(model_id)`。
窗口大小不再依赖模型 ID 字符串猜测（此前 `gpt-4o` 依赖字典序、未知模型一律按 200k、
表只覆盖 10 个前缀）。管线 stage 一律读取 `PipelineContext.context_window`
（由 loop 从 `config.effective_model_context` 注入），与 `request/header` 事件的
`context_size`（`self._config.effective_model_context`）同源同值。

---

## 4. 主动预处理管线（5 级，成本递进）

管线骨架在 `pipeline/base.py`：`PreprocessStage` Protocol + `PreprocessPipeline`
（按 priority 排序、异常不中断、统计 `tokens_saved`、透出 `compact_boundary_created`）。
`PipelineContext` 携带 `model_id`、`context_window`（配置解析后的窗口）、
`overhead_tokens`（system+tools 固定开销）与 `estimated_tokens`（含开销的全量估算）。

执行顺序与触发阈值（均基于配置的 `context_window`）：

| # | Stage | priority | 触发条件 | 动作 | 关键常量 |
| --- | --- | --- | --- | --- | --- |
| 1 | `ToolResultBudgetStage` | 10 | **无条件运行** | 单个 `tool_result` 超 20k tokens 按行截断，保头 500 + 尾 200 | `DEFAULT_TOOL_RESULT_BUDGET=20_000` |
| 2 | `SnipCompactStage` | 20 | `estimated > 窗口×0.60` | 滑动窗口丢最旧消息（不丢 `SystemMessage`），至少保 3 对；目标 = 阈值×0.8；丢弃后 `estimated = 剩余消息 + overhead` | `SNIP_THRESHOLD=0.60`, `MIN_PRESERVE_PAIRS=3` |
| 3 | `MicroCompactStage` | 110 | `estimated > 窗口×0.50` | 只处理倒数 4 轮前的消息：剥 thinking；文本/`tool_result` 超 4k 截断（2/3 头 + 1/3 尾） | `MICROCOMPACT_THRESHOLD=0.50`, `MICROCOMPACT_TURN_LOOKBACK=4`, `MAX_TEXT_BLOCK_TOKENS=4_000` |
| 4 | `ContextCollapseStage` | 150 | `estimated > 窗口×0.75` 且已注入 summarizer | 旧消息（保最近 3 对）交 LLM 摘要，替换为 `compact_boundary` + 摘要消息 | `COLLAPSE_THRESHOLD=0.75` |
| 5 | `AutoCompactStage` | 200 | `estimated >= 窗口×0.80` | <95%：剥全部 thinking + `tool_result` 截到 2k，直到 ≤ `窗口×0.60`；≥95%：只保系统消息 + 边界 + 倒数第 2 条用户消息起的内容 | `AUTO_COMPACT_THRESHOLD=0.80`, `BLOCKING_LIMIT_THRESHOLD=0.95`, `COMPACT_TARGET_RATIO=0.60` |

要点：

- **阈值常量收敛**在 `utils/tokens.py`（本次整改新增 `MICROCOMPACT_THRESHOLD` /
  `COMPACT_TARGET_RATIO`），stage 内不再散落魔数。
- **LLM 摘要器**：`pipeline/summarizer.py::ProviderSummarizer`（输入截 24k 字符）；
  摘要模型取 `context_collapse_model`，未配置则用主模型。默认**关闭**，需
  `EngineConfig.context_collapse_enabled=True` 注入；也可构造时注入整条自定义管线。
- **边界与持久化**：Stage 4/5 创建 `compact_boundary` 后，循环 `store.replace()` +
  `gc_before_boundary()` 并写 `compact/boundary` 事件（`strategy="pipeline"`，带 `kept` 完整窗口）。
  SnipCompact 的丢弃标记也已从误用的 `subtype="api_retry"` 修正为 `compact_boundary`
  （resume 时不再被 `skip_system` 误过滤）。
- **测试**：`tests/test_pipeline.py`（含配置小窗口提前触发用例）、
  `tests/test_context_usage.py`（配置解析 / 分项估算 / overhead 记账）、
  `tests/test_summarizer.py`。

---

## 5. 反应式恢复链（压缩失败后的兜底）

`core/loop.py::_classify_error` → `recovery/chain.py::RecoveryChain`：

| 错误 | 第一次 | 第二次 | 耗尽后 |
| --- | --- | --- | --- |
| `prompt_too_long` | `reactive_compact`（剥 thinking + `tool_result` 截到 4k） | `collapse_drain`（丢最旧 N 对消息，有损） | TERMINATE |
| `max_output_tokens` | 输出上限 8k → 64k | 注入「请继续」恢复提示 ×3 | TERMINATE |
| 其余 | TERMINATE | — | — |

该层不看窗口（已知超限，目标是把体积压下去）；替换窗口时先写
`compact/boundary`（`strategy=reactive_compact / collapse_drain`）再写 `api/retry`。

---

## 6. 上下文使用分析（本次整改新增）

**工具层**：`utils/tokens.py::analyze_context_usage(messages, *, system_prompt, tools, model_context)`
返回：

```python
{
  "context_window": 8000,          # resolve_context_window(model_context)
  "total_tokens": 36,              # system + tools + messages
  "usage_ratio": 0.0045,           # total / window
  "remaining_tokens": 7964,
  "system_prompt_tokens": 7,  "system_prompt_ratio": 0.0009,
  "tools_tokens": 22,         "tools_count": 1,  "tools_ratio": 0.0027,
  "messages_tokens": 7,       "messages_ratio": 0.0009,  "message_count": 1,
  "is_auto_compact_threshold": False,   # 与管线 80% 阈值同口径
  "is_at_blocking_limit": False,        # 与管线 95% 阈值同口径
}
```

**引擎层**：`QueryEngine.analyze_context_usage()` —— 只读方法，取当前
`MessageStore` 快照 + 最近组装的系统提示词（`_last_system_prompt`，未发过请求时即时组装）+
工具 schema，按 `config.effective_model_context` 计算，供 UI/命令/观测复用。

**命令层**：`/context` 输出分项明细与阈值告警，例如：

```
Context: 224 / 200,000 tokens (0.1%), remaining 199,776
- system: 217 tokens (0.1%)
- tools (0): 0 tokens (0.0%)
- messages (1): 7 tokens (0.0%)
```

**Token 追踪集成**：`TokenTracker` 在每次 API usage 落点同步记录上下文快照 ——
引擎在处理带 usage 的 assistant 消息时调用 `record_usage()` 后随即
`analyze_context_usage()` → `token_tracker.record_context_usage()`，并投影
gauge 指标 `nono_agent.context.tokens`（总占用）与 `nono_agent.messages.count`。
查询接口：`get_context_usage()`（最新快照）/ `get_context_usage_history()`
（每步一条，可回看占用趋势）/ `reset()` 一并清理。`SDKResultMessage` 新增
`context_usage` 字段，把最新快照随每轮结果推给客户端。

**TUI / 命令展示**：`observability.token_tracker.format_context_usage()` 把快照
渲染成单行摘要（总量/占比 + system/tools/messages 三分项），`context_usage_color()`
按占用率给出 rich 颜色（<50% 绿 / <80% 黄 / ≥80% 红）。CLI 的 `_consume_stream` 在
每轮 `SDKResultMessage` 汇总行下方打印该行；`/tokens` 命令追加同一行（无快照时不
渲染，如尚未发起任何请求）。

被移除的旧辅助：`calculate_token_warning_state()`（前缀匹配版告警，功能由分析结果的
两个阈值标志承接）；`get_final_context_tokens()` 保留（基于 usage 的精确口径，未接 UI）。

---

## 7. 整改落地记录（对照旧版问题清单）

| 旧版问题 | 状态 | 落点 |
| --- | --- | --- |
| 窗口表硬编码、不可配置、依赖前缀匹配 | ✅ 已整改 | `ProviderConfig.model_context` / `EngineConfig.model_context` + `resolve_context_window()`；前缀表与 `get_context_window_size` 删除 |
| `estimated_tokens` 低估（漏 system prompt 与工具 schema） | ✅ 已整改 | loop Phase1 注入 `overhead_tokens`；`estimate_tools_tokens` / `estimate_system_prompt_tokens`；stage 重估算加回 |
| 阈值常量散落（microcompact 内联 0.50 / auto_compact 内联 0.60） | ✅ 已整改 | `MICROCOMPACT_THRESHOLD` / `COMPACT_TARGET_RATIO` 收敛到 `utils/tokens.py` |
| 缺少上下文使用分析入口 | ✅ 已整改 | `analyze_context_usage()`（工具层 + 引擎层）+ `/context` 命令明细输出 |
| 死代码 `calculate_token_warning_state` | ✅ 已删除 | 功能并入 `analyze_context_usage` 的阈值标志 |
| snip 标记误用 `subtype="api_retry"` | ✅ 已修正 | 改为 `compact_boundary`（与手动压缩一致，resume 不被过滤） |
| `ESCALATED_MAX_TOKENS` 重复定义 | ⏳ 未处理 | `utils/tokens.py` 与 `recovery/max_output_tokens.py` 各一份（无害，待后续合并） |
| `PipelineContext.max_tokens` / `is_retry` 未接线 | ⏳ 未处理 | 仍为预留字段（本次整改已新增 `context_window` / `overhead_tokens` 两个实际生效字段） |
| Stage 1/2/3 构造参数未从 `EngineConfig` 透传 | ⏳ 未处理 | 引擎仍注册默认实例（可注入自定义管线绕过） |

**验证**：全量测试 `413 passed`（`pytest tests/`），新增
`tests/test_context_usage.py`（配置解析优先级 / 无前缀匹配 / 分项加总 / overhead 记账），
扩展 `tests/test_pipeline.py`（配置窗口驱动阈值）；冒烟脚本验证配置解析
（200000 → 32000 → 8000）、引擎分析与 `/context` 输出。

---

## 8. 文件索引

```
nono_agent/
├── pipeline/
│   ├── base.py               # PreprocessStage 协议 / PreprocessPipeline / PipelineContext(context_window, overhead_tokens)
│   ├── tool_result_budget.py # P10  工具结果截断（20k，恒开）
│   ├── snip_compact.py       # P20  滑动窗口丢旧消息（60%，重估算含 overhead）
│   ├── microcompact.py       # P110 剥 thinking + 截长文本（50%，仅旧消息）
│   ├── context_collapse.py   # P150 LLM 摘要折叠（75%，默认关）
│   ├── summarizer.py         # ProviderSummarizer（输入截 24k 字符）
│   └── auto_compact.py       # P200 安全网（80% 温和 / 95% 激进，_reestimate 含 overhead）
├── recovery/
│   ├── chain.py              # RecoveryChain 决策
│   ├── prompt_too_long.py    # reactive_compact / collapse_drain
│   └── max_output_tokens.py  # 8k→64k 升级 + 恢复提示
├── core/
│   ├── engine.py             # _create_default_pipeline / compact / analyze_context_usage / header.context_size
│   ├── loop.py               # Phase1 管线执行（overhead + 消息全量估算）/ 恢复应用 / 错误分类
│   └── config.py             # ProviderConfig.model_context / EngineConfig.model_context / effective_model_context
├── commands/builtin.py       # /context → 分项用量明细
├── settings/                 # 多层配置合并（permissions 等；模型窗口走 EngineConfig/ProviderConfig）
└── utils/tokens.py           # resolve_context_window + 阈值常量 + 估算族 + analyze_context_usage
```
