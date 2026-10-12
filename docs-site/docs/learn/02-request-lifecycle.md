---
title: 02 · 一次对话的完整生命周期
---

# 一次对话的完整生命周期

> 上一篇的最小 Agent 只有二十行。这一篇看同样的循环在 Nono Agent 里长什么样——
> 每一步**为什么必须存在**，以及它在代码里对应谁。

---

## 1. 全景图

一句话概括引擎做的事：

```
用户输入
   ↓
QueryEngine.submit_message()          ← 6 阶段，负责"事务边界"
   ↓
  agentic_loop()                      ← 7 阶段 while 循环，负责"反复试"
   ↓
SDKMessage 流                          ← 消费者（CLI / Server / 库）
```

**两层为什么要分开？**

`QueryEngine` 管的是**一次用户输入的事务**：开头记 `turn/start`，中间校验一致性，结尾给一个
`SDKResultMessage`。`agentic_loop` 管的是**循环里的反复**：调模型、跑工具、决定要不要再来一轮。

分开的直接好处是职责干净——循环不需要知道 turn 是什么，引擎不需要知道循环转了多少次。
下面逐段拆。

---

## 2. 外层：submit_message 的六个阶段

对应 `core/engine.py:418` 的 `submit_message()`。这个方法的 docstring 自己就列了这六步：

| 阶段 | 做什么 | 为什么必须有 |
|------|--------|-------------|
| 1 | 快照配置 + 校验 | 让这一轮用到的门控状态**冻结**，不受运行期环境变化干扰 |
| 2 | 记 `turn/start` + `user/input` | 划出事务边界；进程崩了也知道"这一轮开始了" |
| 3 | 解析 Provider + 组装 system prompt + 写 `request/header` | 把"模型当时被告知了什么"存下来，才能复现 |
| 4 | 驱动 `agentic_loop`，边收消息边记事件 | 循环内部的 step 边界由循环自己上报 |
| 5 | `InternalMessage` → `SDKMessage` | 内外消息模型分离，外部消费者不受内部演进影响 |
| 6 | 记 `turn/end`，产出 `SDKResultMessage` | 这一轮的结束方式有了明确名字 |

### 2.1 阶段 1 为什么"必须冻结"

`QueryConfig` 在方法入口处构造一次，之后整轮不再变。这解决的是一个很具体的坑：

> 循环跑第 8 轮时，某个中间件改了环境变量或某个配置项——
> 后面 5 轮的行为和前面 3 轮就不一样了，而你复现时无从下手。

冻结配置后，"这一轮开始时的世界状态"就是确定的。

### 2.2 阶段 2 的顺序有讲究

看代码时注意这个次序（`engine.py:438-465`）：

```python
# 图片输入门禁 —— 必须在任何事件落盘 / 轮次自增之前。
self._check_attachments_supported(attachments)
```

**先校验，后记账。** 校验失败时不会留下"开了个头但没下文"的半截 turn。
这种"先把门守住，再开门"的顺序，在引擎里反复出现——权限检查在工具执行前、
压缩在 LLM 调用前，道理一致。

紧接着 `await self._flush_events()` 把 `turn/start` 和 `user/input` **立即落盘**。
为什么要马上落？因为进程可能在下一秒就被 Ctrl+C 或断电——先落盘，`--resume` 才认得这次会话。

### 2.3 阶段 2 的一个反直觉设计

代码注释里有这么一段（`engine.py:473-475`）：

> Phase 2: 组装用户消息（模型可见状态），但**不在 loop 前入库**。
> 由 loop 在首个 step 的管线压缩之后追加进 store，避免它落入压缩边界。

也就是说：**用户消息是构造出来了，但不立刻放进消息数组**。要等循环里跑完压缩管线之后再放。

为什么？因为压缩是按"上下文体积"触发的。如果先把用户消息放进去再压缩，
可能被算进"要丢弃的旧消息"里。你刚说的话，在压缩中消失了。

顺序错了，用户输入就可能被自己刚触发的压缩吃掉。

---

## 3. 内层：agentic_loop 的七个阶段

对应 `core/loop.py`。模块 docstring 顶部列了这七步：

| 阶段 | 做什么 | 崩了会怎样 / 为什么存在 |
|------|--------|---------------------|
| 1 | `PreprocessPipeline` 压缩消息 | 不压缩就会撞上下文窗口，见 [上下文压缩](../architecture/context-compression.md) |
| 2 | `MiddlewareChain` 前置钩子 | 让外部代码能在发请求前改消息/提示词/工具 |
| 3 | LLM 流式调用 | 模型输出逐 token 透传，不攒完才给 |
| 4 | 组装 `AssistantMessage` | 把流式片段拼成完整消息 |
| 5 | `PostSamplingChain` 后置钩子 | 采样后还能改结果（比如内容过滤） |
| 6 | `StopHookExecutor` 校验 | 校验不通过可以**阻止**或**阻塞**（让模型重试） |
| 7 | 执行工具 + 转移决策 | 并发安全的并行跑，顺序工具串行跑 |

### 3.1 阶段 7：并行还是串行

这是循环里最实用的一段逻辑。`Tool` 协议上有三个布尔相关的属性：
`is_concurrent_safe`（能不能同时跑多个）、`has_write_side_effects`（有没有写副作用），
以及由后者派生的 `is_read_only`。真正决定调度的只有第一个。

```
模型这一轮返回 3 个工具调用：
  read_file(a.txt)   ┐
  read_file(b.txt)   ├─ 都声明了并发安全 ─→ 用 asyncio.create_task 并行，跑完即产出
  grep("foo")       ┘
  edit_file(a.txt)      ┐
  bash("pytest")       ┴─ 未声明        ─→ 等并发组全部结束后，逐一串行跑
```

**默认 `is_concurrent_safe = False`**（保守）：没声明的、`grep` 出来的顺序执行组，
并行组**先跑完**、串行组**后跑**，保证顺序依赖的工具不会互相穿插。

!!! note "`has_write_side_effects` 不参与调度"
    它只喂给**权限**（要不要问你），不决定并行还是串行。所以两者会交叉：
    `job_kill` 既是并发安全**又**有写副作用 → 走并行组；
    `lsp` / `todo_write` 没有写副作用**但**也没声明并发安全 → 走串行组。

### 3.2 阶段 6 的两种拦截

`StopHookExecutor` 有两种干预方式，别混淆：

- **preventing（阻止）** —— 直接终止循环，模型没机会重试。
- **blocking（阻塞）** —— 往上下文里注入一条错误信息，让模型看到并**重来一轮**。

典型场景：写完代码要求跑测试，hook 发现测试没跑 → blocking，让模型回去跑。
这本质上是**把"评判"放进了循环内部**，而不是留到循环外面做。

### 3.2.1 第三条拦截路径：中间件否决

除了 stop hook 的两种干预，循环还有一条容易漏看的路径：

**`middleware_chain.run_on_transition()` 返回 `None` 即表示否决继续** →
终止 `TerminalReason.HOOK_STOPPED`。

它在两个地方生效（`loop.py:563` 和 `loop.py:712`）：stop-hook 阻塞路径和正常 next-turn 路径。
所以 `HOOK_STOPPED` 不是 stop hook 产生的，是**中间件**否决的——找原因时别只盯 stop hook。

### 3.3 循环的终止：必须给理由

每轮循环结束，代码必须二选一地表明态度。实际代码里，**所有退出点都是
`yield _make_terminal(...)` 紧跟 `return`，一个 `break` 都没有**：

```python
# 继续：构造转移理由
transition = Transition(reason=TransitionReason.NEXT_TURN)
state = LoopState(..., turn_count=state.turn_count + 1, transition=transition)

# 或者终止：给出明确名字
yield _make_terminal(store, state, TerminalReason.COMPLETED)
return
```

`_make_terminal()`（`loop.py:965`）生成一条 `subtype="loop_terminal"`、
内容为 `"Loop terminated: {reason.name}"` 的系统消息；
引擎把这段文本里的理由名解析出来，用在两个地方：

- `turn/end` 事件的 `reason` 字段（**原样保留** `COMPLETED` / `MAX_TURNS` …）
- `SDKResultMessage.subtype` —— 正常完成是 `"success"`，
  否则拼成 `f"error_{reason.lower()}"`，比如 `error_max_turns`

> 所以 `subtype` 里**看不到**完整的原因名：`COMPLETED` 变成了 `"success"`。

`TransitionReason`（`core/state.py`）：

| 理由 | 触发场景 |
|------|---------|
| `NEXT_TURN` | 模型要调工具，工具跑完了，再来一轮 |
| `MAX_OUTPUT_TOKENS_ESCALATE` | 输出撞上限，先把 max_tokens 提到 64k 重试 |
| `MAX_OUTPUT_TOKENS_RECOVERY` | 还是撞上限，注入"请继续输出"提示重试（上限 3 次） |
| `REACTIVE_COMPACT_RETRY` | prompt 太长，反应式压缩成功后重试 |
| `COLLAPSE_DRAIN_RETRY` | 上下文折叠排水后重试 |
| `STOP_HOOK_BLOCKING` | stop hook 阻塞，让模型重来 |
| `TOKEN_BUDGET_CONTINUATION` | token 预算内还有余量，注入提示让它继续干活 |

`TerminalReason`（`core/state.py`）共 11 个：
`COMPLETED` / `ABORTED_STREAMING` / `ABORTED_TOOLS` / `PROMPT_TOO_LONG` / `IMAGE_ERROR` /
`MODEL_ERROR` / `BLOCKING_LIMIT` / `STOP_HOOK_PREVENTED` / `HOOK_STOPPED` / `MAX_TURNS` /
`TASK_BUDGET_EXHAUSTED`。

!!! note "枚举比当前实现更宽"
    枚举定义的是**设计词汇表**，不是"当前每条都走得到"。
    实测：**11 个里有 8 个由循环真实产出**；`PROMPT_TOO_LONG` 与 `BLOCKING_LIMIT`
    在循环里没有构造点，`IMAGE_ERROR` 只在 `recovery/chain.py` 里被赋值
    （而 `RecoveryDecision.terminal_reason` 目前写了没被读）。

    同理，`TransitionReason.TOKEN_BUDGET_CONTINUATION` 目前全仓库**没有构造点**——
    任务预算超限走的是终止 `TASK_BUDGET_EXHAUSTED`，不是继续。

    读代码时如果找不到某个分支，不用怀疑自己：它就是还没接上的那条路。

### 3.4 LoopState 为什么是 frozen

```python
@dataclass(frozen=True)
class LoopState:
    messages: Any                    # 必填：MessageStore
    tool_use_context: Any            # 必填：ToolUseContext
    turn_count: int = 1
    transition: Transition | None = None
    ...
```

（`messages` 和 `tool_use_context` 是**必填**的，构造时必须传；这里只列了其中几项。）

`frozen=True` 意味着**不能就地改**，每次继续循环都要 `LoopState(...)` 构造新实例。
循环里一共只有 **6 处**构造点（`loop.py:571/720/857/878/902/924`），
所有状态转移因此是一份可 grep、可审计的清单。

**但要说清它到底防住了什么**：它防的是**就地悄悄改状态**——比如跑到一半随手
`state.turn_count += 1` 再把同一个对象传下去，这类修改被完全杜绝。

它**不能**防"某个构造点忘了传某个字段"：Python 里漏传的字段会静默取默认值，
类型检查器不会报错。所以准确说法是——

> `frozen=True` 把状态转移变成一份显式的、可审计的构造清单；
> "漏传字段"仍要靠 review 和测试兜。

（`state.py` 的注释目前把这条写成"解决 TS 版漏写字段的 bug"，那指的是**原始 TS 实现**的缺陷；
Python 的 `frozen` 本身并不自动解决它。）

---

## 4. 中间层：谁在上报什么

一个关键问题：`QueryEngine` 和 `agentic_loop` 是两个组件，但事件流要求"覆盖所有改变上下文的路径"。
**谁有权写事件？**

按"谁掌握信息"分成三处：

| 发射者 | 机制 | 它才知道的 |
|--------|------|-----------|
| `QueryEngine` | `_stage_event()` | 循环产出的消息流（assistant / tool / usage / turn 边界） |
| `agentic_loop` | `SessionEventSink` 注入 | 循环内部状态（step 边界、`store.replace()`、重试次数） |
| 工具 / 记忆 | 观察者回调 | 工具内部发生了什么（权限拒绝、记忆读写） |

这不是随意的划分，而是**信息所在的位置**决定的：

> 引擎只能看到循环"吐出来"的东西；循环内部的重试次数它看不见；
> 工具被权限拦下来这件事，引擎和循环都只看到"工具返回了一个错误"。

所以每一类事件都只能由掌握它的那个人来写。细节见
[会话事件溯源](../architecture/session-observability.md)。

### 4.1 引擎初始化里藏着整个架构的一句话

`core/engine.py:322` 的注释值得完整抄下来：

> 事件流就是会话本身：指向一条已有事件流意味着接着这条对话往下走。
> 不重建就会一边追加事件、一边用空上下文请求模型，日志与状态从第一轮起就分叉。

于是 `initialize()` 的逻辑就说得通了：如果 `MessageStore` 是空的，
就从事件流 **hydrate**（重建）一份回来。

> **运行时状态是缓存，事件流才是本体。**
> 这一个视角，能解释掉"事件溯源"带来的所有后续设计——
> 为什么有 `derive_messages()`、为什么有一致性门禁、为什么 resume 就是"读事件流"。

---

## 5. 收尾：为什么内外有两套消息

循环产出的是 `InternalMessage`（引擎内部表示），消费者拿到的是 `SDKMessage`（对外协议）。
转换发生在 `QueryEngine._to_sdk_messages()`。

分开的好处很实际：**内部怎么改都不会打破对外 API**。
今天在 `SDKMessage` 上加一个字段，你所有 CLI / Server / SDK 用户不用改一行。

---

## 继续

- 每个子系统各干什么活 → [03 · 整体架构](03-architecture-overview.md)
- 每个模块的功能点与边界 → [04 · 模块地图](04-module-map.md)
- 为什么选这些技术 → [05 · 技术选型](05-tech-choices.md)