---
title: 01 · Agent 到底是个什么程序
---

# Agent 到底是个什么程序

> 这一页不讲 Nono Agent 的代码，先讲清一件事：**一个能自己调用工具的 LLM 程序，长什么样。**
> 看懂这一页，后面所有的架构设计都会变成"为了解决这里某个具体麻烦"。

---

## 1. 从一次普通的对话开始

我们最容易见到的"AI 对话"，其实很简单：

```python
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,                      # ← 必填
    messages=[{"role": "user", "content": "帮我看看 config.py"}],
)

# content[0] 不一定是文本块——开了 thinking 时它是 ThinkingBlock
text = next(b.text for b in response.content if b.type == "text")
print(text)
```

模型读了你的问题，吐出一段文字。**它没有读文件，没有执行任何东西。**
这段文字看起来很像在回答，但它只是"猜"出来的一段字符。

!!! note
    `max_tokens` 是 Anthropic Messages API 的**必填参数**，不传直接 `TypeError`。
    另外别假设 `content[0]` 就是文本块——开了 extended thinking 时它是 `ThinkingBlock`，
    直接 `.text` 会 `AttributeError`。按 `type` 过滤才是稳的写法。

这构成了 Agent 与普通 LLM 调用的全部差别：

> **模型不能自己做事。它只能输出文字。**
> 要让它真的做事，得有一层程序来读它的输出、真的去执行、再把结果喂回去。

这层程序，就是 Agent 引擎。

---

## 2. 一眼看穿：最小 Agent 长什么样

把上面那段代码加上"执行 + 回喂"，就得到一个 Agent。真的只有这么点：

```python
messages = [{"role": "user", "content": "帮我看看 config.py"}]

while True:                                  # ← 这个循环就是 Agent 的全部本质
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,                     # ← 必填
        messages=messages,
        tools=[read_file_tool, edit_file_tool],   # ← 告诉模型它有哪些能力
    )

    # 模型这次要求调工具了吗？
    if response.stop_reason != "tool_use":
        text = next(b.text for b in response.content if b.type == "text")
        print(text)
        break                               # ← 没有工具调用 = 说完了 = 结束

    messages.append(response)                # 把"我要调工具"记进历史

    for block in response.content:
        if block.type == "tool_use":
            result = execute(block.name, block.input)   # ← 真的去执行
            messages.append({                               # ← 结果塞回对话
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }],
            })
```

**就这样。一个 Agent。** 没有框架，没有插件系统，没有权限沙箱。

请记住这个循环——它叫 **agentic loop**。后面 Nono Agent 的 3000 行 `core/` 本质上就是这个循环，
只是把每一行都做扎实了。架构里所有的"子系统"，都能对应回这上面某一行。

---

## 3. 把最小 Agent 的每个痛点列出来

现在我们来做一件更有价值的事：**假设你要把上面这段代码做成一个真能用的产品，
会遇到什么问题？** 每一条，都是 Nono Agent 里某个包存在的理由。

| # | 痛点 | 在最小 Agent 里 | Nono Agent 的解法 |
|---|------|----------------|------------------|
| 1 | 模型每轮都要看到全部历史，历史会越滚越长，**塞不进上下文窗口** | 直接全量塞 | [`pipeline/`](../architecture/context-compression.md) 分级压缩 |
| 2 | 工具失败了、API 报错了、网络抖了，**程序就崩了** | 直接抛异常 | [`recovery/`](../architecture/nono-agent-architecture.md) 错误分类 + 自动重试 |
| 3 | 模型可能**连着调 20 次工具**才出结论，中途你没任何感知 | 沉默地跑 | [`AsyncGenerator`](../architecture/nono-agent-architecture.md) 流式产出 |
| 4 | 程序跑崩了 / 你想反悔，**之前的状态全丢了** | 没得救 | [`session/`](../architecture/session-event-catalog.md) 事件溯源，可 resume / fork |
| 5 | 模型可能执行 `rm -rf`，**谁能拦住它** | 拦不住 | [`permissions/`](../guide/tools-and-permissions.md) 权限模式 + 沙箱 |
| 6 | 想接一个新模型供应商，**要改引擎代码吗** | 要改 | [`providers/`](../architecture/nono-agent-architecture.md) 三协议适配器 |
| 7 | 想给别人一批工具，**只能改主程序** | 要改 | [`toolplugins/`](../guide/usage.md) 声明式插件 |
| 8 | 上一轮说了什么、这一轮花了多少钱，**事后查不到** | 查不到 | [`observability/`](../architecture/session-observability.md) 事件流投影成指标 |
| 9 | 上下文塞进了密钥，**日志外发就泄漏了** | 会泄漏 | [`session/redact.py`](../architecture/session-observability.md) 读取侧脱敏 |

---

## 4. 三个必须先建立的概念

看懂上面的循环之后，有三个概念是理解整个架构的钥匙。它们在最小 Agent 里是隐含的，
在 Nono Agent 里被显式建模了。

### 4.1 模型看得见的东西 ≠ 你以为的东西

模型每次收到的，是**一整个消息数组**。它没有记忆，跨轮次的连续性完全靠你每次重新构造这个数组实现。

这带来一个反直觉的推论：

> **"Agent 记得什么"，严格等于"上一轮你构造出来的那个数组是什么"。**

所以只要有一处代码偷偷往数组里塞了点东西、或者把某段删了——模型的行为就会变，
而你在代码里几乎看不出原因。这是 Agent 开发里最常见、也最难查的一类 bug。

Nono Agent 对这个问题的回答很硬：**模型看得见的每一样东西，都必须先写进事件流**，
消息数组只允许由事件流推导出来。见 [会话事件溯源](../architecture/session-event-catalog.md)。

### 4.2 "轮次"和"步"是两件事

一次用户输入（一次 `submit_message`），模型可能需要：

```
用户："帮我把这个 bug 修掉"
        ↓
第 1 步：调 read_file 读文件
        ↓
第 2 步：调 grep 搜索相关代码
        ↓
第 3 步：调 edit_file 改代码
        ↓
第 4 步：调 bash 跑测试
        ↓
第 5 步：不再调工具，输出结论 —— 结束
```

**一次用户输入 = 1 个 turn；一次 LLM 请求 = 1 个 step。** 一个 turn 里有几个 step 由模型自己决定。

新手最容易在这里把日志和统计看糊涂，所以 Nono Agent 从一开始就把
`turn/start` / `step/start` 分成两种事件，而且刻意让它们用**各自独立的计数器**
（`turn/*` 用 turn 号，`step/*` 用循环自己的 `LoopState.turn_count`），不混用。

### 4.3 终止必须有名字

Agent 停下来，可能是正常答完了，也可能是炸了、超时了、被用户 Ctrl+C 了、被 hook 拦了。

如果代码里只有一句 `break`，那你**永远不知道这一轮到底是哪种情况**——
日志里、UI 上、SDK 返回值里，全都说不清。

Nono Agent 把"为什么停"建模成一个 11 项的枚举 `TerminalReason`，把"为什么继续"建模成
7 项的 `TransitionReason`，每次循环都必须二选一。见 [状态机](../architecture/nono-agent-architecture.md)。

---

## 5. 一个立刻能用的判断标准

读任何 Agent 代码时，问自己三个问题，基本就能判断它的质量：

1. **它是怎么保证模型看到的上下文可复现的？**
   如果答案是"内存里维护一个数组"，那你迟早会遇到"我明明没改代码，它怎么变了"。

2. **它是怎么处理"这一轮为什么结束"的？**
   如果答案里没有明确的原因枚举，出线上问题时你只能靠猜。

3. **工具默认是安全的，还是默认是方便的？**
   一个默认"直接执行"的文件写入工具和一个默认"先问你"的工具，
   决定了你的 Agent 是能放心跑在生产，还是只能跑在玩具环境。

Nono Agent 在这三点上的选择，以及**为什么这么选**，是接下来几页的主题。

---

## 继续

- 想看这些概念在真实代码里怎么落地 → [02 · 一次对话的完整生命周期](02-request-lifecycle.md)
- 想知道整个系统由哪些模块拼成 → [03 · 整体架构](03-architecture-overview.md)