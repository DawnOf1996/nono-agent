# 单轮 token 用量 vs 底部上下文占用：口径分析

> 分析对象：会话 `dc417842-49b5-4314-b87c-1acab2af6780`（标题「1」）
> 事件流：`~/.nono-agent/sessions/dc417842-49b5-4314-b87c-1acab2af6780.jsonl.zst`，188 条事件
> 结构：**1 轮 / 8 步**（8 次 API 调用，含 9 次工具调用往返）
> 注：分析期间该会话仍在被写入（第一轮 8 步稳定，第二轮又追加了 2 步）。
> 本文档的结论基于**第一轮**的 8 条 usage 事件（seq 22–186），这批数据是静态的。

---

## 1. 结论先说

两处显示的数字**本来就不是同一个量**，而且没有任何代码试图让它们相等：

| 位置 | 数值 | 含义 |
|---|---|---|
| 消息气泡下方「本轮用量」 | **45,220 tok** | 本轮 8 次 API 调用的**累计计费量**（cost） |
| 底部「上下文已用」 | **4,775 / 1,000,000 = 0.48%** | 最后一次请求**单次**携带的上下文大小（occupancy） |

差距约 **9.5 倍**。下面拆成三个独立的成因。

---

## 2. 原始数据

本轮 8 次调用的 `usage` 事件（seq 22 → 186）：

```
 seq   input  cacheRead    out | REQ_INPUT | ctx_estimate   ratio
  22    2997       3185    101 |     6182 |         2815  0.0028
  40      37       3321     64 |     3358 |         2847  0.0028
  64     658       4041    153 |     4699 |         3315  0.0033
  91     255       4447    251 |     4702 |         3536  0.0035
 112      81       4777    183 |     4858 |         3609  0.0036
 143     815       5773    438 |     6588 |         4479  0.0045
 170      25       6234    450 |     6259 |         4666  0.0047
 186      57       6739    138 |     6796 |         4775  0.0048

SUM of input        =  4,925
SUM of cache_read   = 38,517   <-- 被重复累加的就是它
SUM of output       =  1,778
SUM of all four     = 45,220
```

`REQ_INPUT = input + cache_read + cache_creation`，即**这一次调用实际发出去的完整输入量**。

---

## 3. 成因一：`cache_read_input_tokens` 在一轮内被重复累加（主因）

看 `cacheRead` 那一列：3185 → 3321 → 4041 → 4447 → 4777 → 5773 → 6234 → 6739。

**它在单轮内单调递增**，而不是一个固定值。

原因是 agentic loop 的工作方式：模型调用工具 → 工具结果回填 → 再次请求模型 → …。每一步都要**把整段对话重新发一遍**，所以 provider 每次都报告「本次从 prompt cache 读回了整个已缓存前缀」。第 8 次调用读回 6,739 个 token，指的是**整段会话**，不是「第 8 次新增的 6,739 个」。

于是 `SUM(cache_read) = 38,517` 把同一段上下文数了 8 遍。

正确的「当前上下文有多大」应该取**最后一次**：`57 + 6739 = 6,796`（provider 口径）或 `4,775`（引擎估算口径，见成因三）。

> 注意：作为**计费量**，38,517 是对的 —— provider 确实按次收了这笔钱。问题只在于把它当「上下文占用」用。

---

## 4. 成因二：两者本就是不同维度的指标

- **本轮用量**：把一轮内所有 API 调用的四桶全部相加，回答「这一轮我花了多少 token」。
- **上下文占用**：单次请求 payload 的大小，回答「窗口被占了多少、离压缩还有多远」。

一轮里的 step 越多，两者差距越大，倍数约等于 loop 迭代次数。本轮 8 步 → 约 9.5 倍（含 output 桶和其他误差）。

所以即使修掉成因一，这两个数字也**不会相等**，因为它们回答的是两个问题。

---

## 5. 成因三：估算口径与 provider 口径本身有偏差

同一个时刻（第 8 步）：

- provider 口径：`input + cache_read = 57 + 6739 = 6,796`
- 引擎估算口径（`analyze_context_usage`）：`system 194 + tools 2,605 + messages 1,976 = 4,775`

**provider 比引擎多算 2,021 token（+42%）。**

根因在 `nono_agent/utils/tokens.py:29-38`：

```python
def count_tokens(text: str) -> int:
    encoder = _get_encoder()
    if encoder is not None:
        return len(encoder.encode(text))
    return max(1, len(text) // 4)     # ← 实际走的是这条
```

实测当前环境 **没有安装 tiktoken**：

```
tiktoken encoder available: False
tiktoken import failed: No module named 'tiktoken'
```

于是所有估算退化为 `len(text) // 4`。对自然语言这个近似尚可，对**工具 JSON schema** 这种标点密集的文本会**明显低估**（本轮 tools 一项就估了 2,605）。

所以即使口径统一，估算值与 provider 实测值之间仍有稳定误差。

> **状态：已修复。** tiktoken 已提升为 `nono-agent` 的硬依赖（`pyproject.toml`），
> 并已装进三个开发 venv。桌面端 sidecar 另做了离线兜底：构建期预取 BPE 词表
> 随包分发（`scripts/build_sidecar.py`），运行期由
> `apps/server/packaging/tiktoken_runtime_hook.py` 把 `TIKTOKEN_CACHE_DIR` 指向它；
> 词表拿不到时 `_get_encoder()` 记一次失败后退回启发式，**不会**在每条消息上重试联网。
> 覆盖见 `tests/test_tiktoken_loader.py`（8 个用例，含断网降级与「只试一次」）。

---

## 6. 附带发现：0.48% 被四舍五入显示成 0%

`apps/web/src/lib/usage.ts` 的 `formatPercent`：

```ts
export function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}
```

`usage_ratio = 0.0048` → `Math.round(0.48)` = `0` → 界面显示 **「0%」**。

新会话 / 小上下文时这个角标会恒显示 0%，看不出真实占用。参考图里是 14%，整数显示没问题；这里只是刚好踩到了精度边界。

---

## 7. 现有计算逻辑

### 7.1 每轮用量（消息气泡角标）

**数据来源**：`apps/web/src/api/sse.ts` → `messagesFromEvents()`

在事件流里遇到 `usage` 事件时累加四桶，遇到 `turn/end` 时挂到该轮 AI 消息上：

```ts
const addUsage = (incoming: TurnTokens) => {
  if (!pendingUsage) { pendingUsage = { ...incoming }; return; }
  pendingUsage.inputTokens        += incoming.inputTokens;
  pendingUsage.outputTokens       += incoming.outputTokens;
  pendingUsage.cacheReadTokens    += incoming.cacheReadTokens;
  pendingUsage.cacheCreationTokens += incoming.cacheCreationTokens;
  if (incoming.model) pendingUsage.model = incoming.model;
};
```

角标总数 = 四桶直接相加（`usageTotal`）：

```ts
export function usageTotal(usage: TurnTokens): number {
  return usage.inputTokens + usage.outputTokens
       + usage.cacheReadTokens + usage.cacheCreationTokens;
}
```

### 7.2 会话统计（底部三枚角标）

**数据来源**：`GET /session/{id}/stats` → `nono_agent/session/stats.py` → `build_session_stats(events)`

服务端按 `step/start` → 内容事件 → `step/end` 把每个 step 切三段：

```
step ─┬─ 等首 token ─┬───── 生成中 ──────┬── 工具执行 ──┬─
      └── TTFT ─────┘    └── model_ms ───┘   └ tool_ms ─┘
```

| 指标 | 公式 | 本轮实测 |
|---|---|---|
| 轮 / 步 | `turn/start` / `step/start` 计数 | 1 / 8 |
| 模型用时 | Σ `末条内容 − step开始` | 27,524 ms |
| 工具用时 | Σ `step结束 − 末条内容` | 10,649 ms |
| TTFT | mean(`首条内容 − step开始`) | 1,014 ms |
| TPS | `Σ输出 ÷ Σ(首条内容→末条内容)` | 91.6 tok/s |
| Token 总量 | 四桶求和 | 45,220 |
| 缓存命中 | `读回 ÷ (读回 + 新鲜输入)` | 88.66% |
| 上下文 | 最后一条 `usage` 事件的 `context` 快照 | 4,775 / 1M |

其中「上下文」一栏取自引擎写入 `usage` 事件的 `context` 字段：

```python
self._stage_event(
    EVENT_USAGE,
    usage=usage_dict,
    model=self._config.effective_model,
    context=context_usage,      # analyze_context_usage() 的结果
)
```

**老日志没有 `context` 字段时**，回退到「最后一次请求的输入三桶之和」：

```python
last_input_total = (usage["input_tokens"]
                  + usage["cache_read_input_tokens"]
                  + usage["cache_creation_input_tokens"])
```

本例有 `context`，走的是 4,775 而非 6,796。

---

## 8. 界面上实际会显示成什么

| 位置 | 渲染 | 结果 |
|---|---|---|
| 消息气泡 | `formatTokensCompact(45220)` | `用量 45.2k tok` |
| 底部「会话统计」 | `1轮 8步 · 92 tok/s` | 正常 |
| 底部「Token 用量」 | `45.2k tok · 缓存命中 89%` | 与气泡一致 |
| 底部「上下文」 | `formatPercent(0.0048)` | **`0%`** ← 被取整抹平 |

---

## 9. 可选的修法（未实施，供分析）

按影响从大到小：

1. **给「上下文」换精度**：`usage_ratio < 0.01` 时保留一位小数（`0.5%`），成本最低。
2. **明确两个指标各自的语义**，别让用户以为该相等：气泡角标可改成「本轮花费 45.2k」，上下文角标标注「单次请求」。
3. **给 `usage` 事件补一个「本次新增上下文」字段**，或让 `cache_read` 改报增量 —— 需要 provider 层配合，属于后端改造。
4. **装上 tiktoken**，让 `analyze_context_usage` 的估算与 provider 口径接近，可消除约 42% 的系统性低估。
5. 若确实想比较「一轮花了多少 vs 上下文多大」，应改用**最后一次**的 `input + cache_read`（本例 6,796）作为对比基准，而不是四桶求和。

---

## 复现方式

```powershell
$py = "apps\server\.venv\Scripts\python.exe"
& $py -c @"
import sys, json, asyncio
sys.path.insert(0, '.')
from nono_agent.session.transcript import LocalFileTranscriptWriter
from nono_agent.session.stats import build_session_stats
from pathlib import Path
p = Path.home()/'.nono-agent'/'sessions'/'dc417842-49b5-4314-b87c-1acab2af6780.jsonl.zst'
events = asyncio.run(LocalFileTranscriptWriter(p).read_events())
print(json.dumps(build_session_stats(events), indent=2, ensure_ascii=False))
"@
```
