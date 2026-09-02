# Generative Agents 工具化 —— nono-agent TODO 规划

> **文档角色：** 对照 Park et al. *Generative Agents*（UIST 2023, [arXiv:2304.03442](https://arxiv.org/abs/2304.03442)）与当前 `nono_agent/` 代码，给出「小镇 NPC = 独立 QueryEngine 实例、认知行为走工具、游戏引擎只注入工具使用提示词」所需的新增 / 改动 / 重构清单。
> **不是：** 游戏引擎（地图、时钟、寻路、对象状态）的实现计划。那些属于 simulated-town / 世界层。
> **与已有文档关系：** [Memory-System-Design.md](./Memory-System-Design.md) 描述的是「引擎在组 prompt 时自动 `retrieve()` 注入」的编码助手路径。本文是另一条产品路径：**认知原语暴露为工具，由模型按提示词自行调用**。两条路径应共存，用配置切换，而不是互相覆盖。

---

## 1. 目标架构（验收口径）

每个 AI 人物是一个 **独立的** `QueryEngine`：独立 `EngineConfig`、独立 `session_id` / transcript、独立记忆文件。游戏引擎每拍只做三件事：

1. 把本拍观察（时间、地点、附近的人、收件箱）写成用户消息（或提供世界只读工具）。
2. 注入**工具使用提示词**（何时 `memory_write` / `memory_retrieve` / `reflect` / `plan_`*，何时调用世界工具）。
3. 执行世界层副作用（移动、对话投递、对象状态）。**不**在导演循环里调用 `MemoryManager.admit()` / `retrieve()` / `reflect()`。

认知闭环全部发生在该人物自己的 agentic loop 里：模型决定记什么、查什么、何时反思、如何改计划。

```
游戏引擎                         nono-agent（每人一个实例）
────────                         ─────────────────────────
时钟 / 地图 / 对象状态            QueryEngine + 独立 session
观察 → user message               记忆文件（独立 MemoryStore）
世界工具：move / talk / perceive   认知工具：memory_* / reflect / plan_*
注入「何时用哪把工具」的提示词      MemoryManager（工具内部调用，导演不碰）
串行落库世界变更                  transcript 事件流（含 memory/*、tool/*）
```

**明确不放进 nono-agent 的东西：** 环境树、寻路、对象状态机、多 NPC 调度、对话回合的世界同步。论文 §5 的 sandbox server 仍归游戏。

---



## 2. 论文能力 vs 现状对照

论文架构三件套（§4）：**Memory Stream、Reflection、Planning**。检索公式为 `score = α·recency + β·importance + γ·relevance`。消融实验证明三者缺一不可。


| 论文能力            | 论文做法                                                    | nono-agent 现状                                                              | 相对本目标                                    |
| --------------- | ------------------------------------------------------- | -------------------------------------------------------------------------- | ---------------------------------------- |
| 每人独立经验库         | 每 agent 一条 memory stream                                | `EngineConfig.agent_id` + 可注入 `MemoryStore`；默认却是全局 `~/.nono-agent/memory/` | 实例隔离可用，缺**一等公民默认路径**                     |
| 观察写入            | 每拍感知写入 stream                                           | `MemoryManager.add_observation()` Python API                               | **无工具**；小镇导演也不写观察                        |
| 检索              | 按当前情境取 top-k 进 prompt                                   | `_build_system_prompt` **自动** `retrieve(query=用户话)`                        | 引擎代劳，模型**不能按需查询**                        |
| recency（时效性）    | 距**上次取回**的指数衰减                                          | 用 `created_at`，且 `last_access_at` **从不回写**                                 | 与论文不一致                                   |
| importance（重要性） | 写入时 LLM 打 1–10 分                                        | 调用方手填；无打分钩子                                                                | 缺自动打分                                    |
| relevance（相关性）  | embedding 余弦                                            | Jaccard 词面；`embedding` 字段闲置                                                | P1 增强，不挡工具化                              |
| 反思              | 重要性累计 >150 → 生成 3 个问题 → 分别检索 → 带引用的 insight 回写，可递归（反思树） | `reflect()` = 最近 N 条 episodic 一次摘要成 `meta`                                 | API 过粗，且**只能 Python 调用**                 |
| 规划              | 自上而下：日计划 → 小时 → 5–15 分钟；计划本身写入 stream；遇反应则从该时刻起再生       | `enter_plan_mode` = **编码只读权限**；`todo_write` = 会话内任务清单，不落记忆                 | **命名冲突 + 能力缺失**                          |
| 反应              | 每拍：继续计划 vs 反应；对话用「与对方关系」检索                              | 无                                                                          | 用提示词 + `memory_retrieve` + 世界工具即可，不必引擎内建 |
| persona / 种子记忆  | 分号句写入初始 memories                                        | `custom_system_prompt` 或手写 store                                           | 缺 `kind=persona` 恒常注入；缺种子导入 API          |


**已经够用、不必为小镇重做的部分：**

- 一人一 `QueryEngine`（simulated-town `agent_factory.py` 已验证）。
- `MemoryRecord` envelope（id / kind / agent_id / importance / source_ref / supersedes）。
- 三因子检索骨架、`memory/write` 与 `memory/retrieve` 事件、SQLite 后端。
- 工具协议、`ToolRegistry` 可只注册子集（小镇可以不挂 Bash）。
- `append_system_prompt` / `custom_system_prompt` 可供游戏注入用法说明。

---



## 3. 设计抉择（先锁这两条，再开工）



### 3.1 自动注入 vs 工具检索：配置切换，默认保持现状

编码助手路径依赖「组 prompt 时静默召回」。小镇路径要求模型显式 `memory_retrieve`。

建议 `EngineConfig.memory_injection: "auto" | "tools" | "hybrid"`：


| 模式              | 行为                                               | 适用         |
| --------------- | ------------------------------------------------ | ---------- |
| `auto`（默认，兼容现状） | 组 prompt 时按用户话检索注入                               | CLI / 编码助手 |
| `tools`         | **不**按 query 自动检索；仅恒常注入 `persona`（及可选 `meta` 摘要） | 小镇 NPC     |
| `hybrid`        | persona 恒常 + 自动 top-k，**同时**暴露检索工具               | 过渡 / 对照实验  |


验收：`tools` 模式下，未调用 `memory_retrieve` 则本轮 system prompt **不得**出现昨日 episodic；调用后工具结果里出现，且 `memory/retrieve` 事件带命中 id。

### 3.2 厚工具 vs 薄原语：原语必做，论文管线做可选厚工具


| 层      | 工具                                                                  | 谁做推理                           |
| ------ | ------------------------------------------------------------------- | ------------------------------ |
| 原语（必做） | `memory_write` / `memory_retrieve` / `memory_get` / `memory_retire` | 模型按提示词编排                       |
| 编排（P1） | `reflect`（问题→检索→带引用 insight）                                        | 工具内跑论文 §4.2，内部仍走 MemoryManager |
| 编排（P1） | `plan_compose` / `plan_revise`                                      | 工具内递归分解，计划以 `kind=plan` 落库     |


游戏引擎**两层都不实现**。提示词只写触发条件：例如「重要性累计偏高时调用 `reflect`」「早晨先 `plan_compose`」「看见熟人先 `memory_retrieve` 再决定是否 `talk_to`」。

不把反思/规划做成「导演在时钟 23:00 调 Python」。那样违反本目标。引擎最多在 `tools` 模式下于 prompt 附一行统计（近期 importance 之和），仍由模型决定是否调用工具。

---



## 4. 分项 TODO：新增 / 改动 / 重构

工作量标记：S < 0.5 日，M 1–2 日，L 3–5 日。优先级 P0 = 小镇工具闭环能跑；P1 = 对齐论文 believability；P2 = 质量与成本。

### 4.1 新增



#### P0-1 认知工具包 `tools/builtin/memory.py`（L）

新增工具（均通过 `ToolUseContext` 取 `MemoryManager`，禁止工具直接 new store）：


| 工具                | 写副作用               | 输入要点                                                                       | 行为                                                                   |
| ----------------- | ------------------ | -------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `memory_write`    | 是                  | `content`, `kind?`, `importance?`, `keys?`, `supersedes?`, `evidence_ids?` | 填 `agent_id`/`session_id`，`admit()`；缺 importance 时走可注入 scorer，否则默认 5 |
| `memory_retrieve` | 否（但回写 last_access） | `query`, `k?`, `kinds?`                                                    | 三因子检索；更新 `last_access_at`/`access_count`；返回 id/kind/score/content    |
| `memory_get`      | 否                  | `memory_id`                                                                | 按 id 读一条                                                             |
| `memory_retire`   | 是                  | `memory_id`                                                                | 软删 `retired`（不要默认硬删）                                                 |


`create_cognitive_tools()` 独立于 `create_builtin_tools()`。小镇只挂认知包 + 世界工具；CLI 编码助手可按配置加挂认知包。

测试：同实例 write→retrieve 命中；`agent_id` 隔离；无 manager 时工具报错而非静默空。

#### P0-2 认知工具使用提示词 section（M）

新增 `prompt/sections/cognitive_guidance.py`（或扩展 `tools_guidance` 按已注册工具名分支）。**仅当注册了认知工具时渲染**，避免继续对编码助手灌「去小镇里回忆」。

游戏引擎用 `append_system_prompt` 补场景触发词即可，nono-agent 提供默认可覆盖文本：

- 感知到事件 → `memory_write`（`episodic`）
- 决策 / 对话前 → `memory_retrieve`
- 对某人形成稳定看法 → `memory_write`（`semantic` / `meta`）
- 日计划用 `kind=plan` 写入，执行中用 retrieve 核对

当前 `tools_guidance.py` 写死了 read/edit/shell，小镇实例若误挂会把 NPC 导向改文件。见 R-2。

#### P0-3 `ToolUseContext` 携带记忆句柄（S）

`ToolUseContext` 增加只读字段（或约定 `extra["memory_manager"]` + `extra["agent_id"]`，**推荐一等字段**以免每个工具自己掏 extra）：

- `memory_manager: MemoryManager | None`
- `agent_id: str | None`

`QueryEngine.submit_message` 构造 context 时注入。这是工具能调用记忆的唯一合法通道。

#### P0-4 `kind=plan`（S）

`VALID_KINDS` 增加 `plan`。计划是 memory stream 的一等公民（论文 §4.3），不要复用 `todo_write`，也不要复用 `enter_plan_mode`。

`MemoryRecord` 增加可选 `evidence_ids: list[str]`（反思引用）。现有 `source_ref` 指向 session 事件，引用的是另一条记忆，语义不同。

#### P0-5 每实例默认记忆目录（S）

当调用方**未**注入 `memory_store` 且配置了 `agent_id` 时：

- 文件系统：`~/.nono-agent/agents/<agent_id>/memory/`
- 或 `EngineConfig.memory_path` 显式指定（小镇已用 `data/<id>/memory.db`，应继续可注入）

未设 `agent_id` 时保持今日全局目录，避免破坏 CLI。

#### P1-1 厚工具 `reflect`（M）

把 `MemoryManager.reflect()` 从「N 条摘要」改成论文管线（可先内部函数，工具薄封装）：

1. 取最近若干条（论文 100；可配置）。
2. 生成 3 个高层次问题（需 LLM；复用已有 summarizer / provider）。
3. 每个问题 `retrieve()`。
4. 抽取 insights，格式 `insight (because of id…)`，写入 `kind=meta` + `evidence_ids`。
5. 允许对已有 `meta` 再反思（树）。

触发**不**由导演定时调用。工具可在结果中附带 `recent_importance_sum`，提示词建议超过阈值时调用。

现有「一次性摘要」可留作 `reflect(mode="summarize")` 回退，避免砸现有单测。

#### P1-2 厚工具 `plan_compose` / `plan_revise`（M）

- `plan_compose`：输入「今天日期 + 昨日摘要 query」；内部 retrieve persona/meta/昨日 plan；产出 5–8 段日计划，再按配置递归到小时块；全部 `memory_write(kind=plan)`。
- `plan_revise`：输入「从某时刻起发生了什么」；retire/supersede 未执行的 plan 段，写新计划。

粒度（15 分钟 vs 小时）做成 `EngineConfig` 字段，禁止在工具里写死魔法数（项目约定：部署相关量走 Config）。

递归分解若每层都打模型，成本会很高（论文也承认）。P1 先日计划+小时；15 分钟块放到 P2。

#### P1-3 importance 自动打分钩子（S）

`MemoryManager` 可注入 `ImportanceScorer`。`memory_write` 未传分时调用；无 scorer 则默认 5。论文用一次短 prompt 打 1–10。不要在 `admit()` 里隐式打外部 API（失败应显式返回工具错误）。

#### P2-1 embedding relevance（L）

按 [Memory-System-Design.md](./Memory-System-Design.md) 原 P1：用余弦替换/融合 Jaccard。工具化不依赖此项；检索质量差时再上。

#### P2-2 候选池真正走 `candidate`（M）

当前 `admit()` 把低 importance 直接 `rejected`。工具写入的反思/观察可先 `candidate`，再按门槛转 `accepted`。防止模型把每句「路过一张椅子」都写成永久事实。

---



### 4.2 改动



#### C-1 检索 recency 对齐论文（S）——正确性，建议随 P0-1 做

`retrieve()` 改为 `now - (last_access_at or created_at)`，命中后回写 `last_access_at`/`access_count`。论文明确 recency 跟「上次被取回」走，否则旧但刚被想起的记忆会错误衰减。

衰减时钟：论文用**游戏小时**。`MemoryManager` 增加可选 `now`（已有）即可；小镇在观察里带游戏时间，retrieve 传入游戏 epoch，避免用墙钟把「过了一夜」打成「过了 30 秒」。

#### C-2 `_build_system_prompt` 按 `memory_injection` 分支（M）

- `auto`：保持现逻辑。
- `tools`：只拼 persona（及可选恒常 meta）；**不要**用本轮 user query 做检索。否则工具化会被自动注入短路，模型误以为「不用查也记得」。
- 自动检索触发的 `memory/retrieve` 事件在 `tools` 模式下不应再每拍打一次空查询。



#### C-3 `MemoryManager.reflect` 签名与行为（M）

现实现：无 summarizer 返回 `[]`；吞掉 summarizer 异常。工具路径上失败必须返回 `ToolResult.is_error`，不能空成功。

补：`cited` 解析失败时仍落库 insight，但 `evidence_ids=[]` 并在内容里标明「未解析到引用」。

#### C-4 `/memory` slash 与观测（S）

`/memory` 现仅列表。至少加 `retrieve <query>` 只读调试，方便对照工具路径。不必做完整管理面。

#### C-5 `EngineConfig` 补齐记忆/认知旋钮（S）

已有：`agent_id`、`memory_retrieval_k`、`memory_retrieval_token_budget`。

建议增加：

- `memory_injection`
- `memory_path: Path | None`
- 打分权重 α/β/γ（现仅 Manager 构造参数，小镇换权重必须 new Manager）
- `reflection_importance_threshold`（只用于 prompt 提示与 stats，不自动开火）

无 hardcoded tunables：阈值进 Config。

#### C-6 小镇侧（simulated-town，非本仓库必改，但验收依赖）

现 `agent_factory.py` 默认 **json_schema 动作**，真实模型默认**不注册工具**（`TOWN_REGISTER_TOOLS`）。这与「工具驱动认知」相反。

小镇后续应：

- `memory_injection="tools"`
- 注册认知工具 + 世界工具
- 去掉（或降级）「只输出一个 JSON」提示
- 导演**停止**解析 ACTION_SCHEMA 作为唯一动作通道；世界变更以工具结果为准
- 观察字符串里加工具使用提醒，而不是 `recall` 这种假动作枚举

本文件不把小镇重构算进 nono-agent P0，但 P0 验收应用一个最小 example（见 §6）。

---



### 4.3 重构



#### R-1 工具包拆分，禁止「内置 = 编码工具全集」（M）

`create_builtin_tools()` 现在是 Bash/Read/Write/… 一锅端。认知工具若塞进去，每个 CLI 会话都会多 4–6 个与写代码无关的 tool schema。

建议：

```
create_coding_tools()      # 今日 create_builtin_tools
create_cognitive_tools()   # 记忆/反思/规划
create_builtin_tools()    # 兼容别名 → coding；changelog 写明
```

CLI 默认仍只挂 coding。库调用方显式组合。`AgentDefinition.tools` 白名单可点名 `memory_retrieve`。

#### R-2 `tools_guidance` 按已注册工具生成，不要写死工作区编辑（S）

今日静态英文段落假设用户在 workspace 里改文件。小镇 NPC 会收到错误行为先验。

改为：根据 `PromptContext.tools` 的 name 列表选择 guidance 块（coding / cognitive / 两者都有）。无匹配则省略。

#### R-3 依赖方向：允许 `tools/builtin → memory` 的 Manager 协议，禁止碰 Store 内部（S，文档+代码）

[ROADMAP.md](./ROADMAP.md) §5.3 写 `prompt → memory 的已加载视图`，且暗示 memory 不反向进工具。工具化后应改成：

```
tools/builtin/memory_*  ──► memory.MemoryManager（公开 API）
memory                  ↛  tools.executor / 具体世界工具
core                    ──► 把 manager 塞进 ToolUseContext
```

`MemoryStore` 仍只被 Manager 与测试使用。

#### R-4 计划能力命名去冲突（S，文档为主）


| 现名                                   | 实际含义        | 小镇该用         |
| ------------------------------------ | ----------- | ------------ |
| `enter_plan_mode` / `exit_plan_mode` | 权限模式：禁写副作用  | 保持，NPC 默认不注册 |
| `todo_write`                         | 本会话待办，不持久   | 可选；**不是**日计划 |
| 新 `kind=plan` + `plan_*` 工具          | 持久、可检索的未来行为 | 论文 planning  |


文档与 JSDoc 写清，避免后续把日计划接到 permission PLAN 模式上。

#### R-5 自动检索与工具检索的事件语义（S）

今日组 prompt 的自动 retrieve 也会发 `memory/retrieve`。`tools` 模式下观测面板应能区分「prompt 注入召回」与「模型主动召回」。事件载荷加 `source: "prompt" | "tool"`（envelope 增量，不需要 bump session 格式版本——属于可忽略扩展的话按现有 ignorable 规则处理）。

---



## 5. 建议不在本阶段做的事


| 项                             | 原因                             |
| ----------------------------- | ------------------------------ |
| 把世界树 / 寻路 / 对象状态做进 nono-agent | 论文 §5 明确是 sandbox；会把引擎绑死在一种游戏上 |
| 导演循环里定时 `reflect()`           | 违反「工具驱动、引擎只注入提示词」              |
| 用 `todo_write` 冒充日计划          | 不落盘、无时间地点、无法被三因子检索             |
| 用子 Agent `AgentTool` 模拟每个 NPC | 子 Agent 是父会话里的委派，不是独立记忆/会话文件   |
| 先上 embedding / FTS5           | 不阻塞工具闭环；启发式已能跑通隔离与写入           |
| 群组共享记忆                        | 论文里信息传播靠对话，不靠共享 DB；那是 P2 产品选择  |
| 改 agent-loop 为「感知-计划-行动」固定状态机 | 项目约定新行为走工具与 prompt，不改 loop     |


---



## 6. 分阶段落地与验收



### P0 —— 工具闭环（nono-agent 可被小镇接上）

- [ ] 认知工具包 + context 注入 + `kind=plan` + `evidence_ids`
- [ ] `memory_injection="tools"`：无自动 episodic 注入；persona 仍在
- [ ] recency 回写 last_access；可用游戏时间 `now`
- [ ] `create_cognitive_tools` 与 coding 包分离；guidance 按工具列表渲染
- [ ] 每 `agent_id` 默认记忆路径（仍允许注入 store）
- [ ] 单测：双实例隔离；write/retrieve/retire；`tools` 模式 prompt 不含未检索 episodic
- [ ] **可运行 example**（`examples/` 或测试夹具）：两个 FakeProvider/真实模型 agent，无游戏地图，只靠观察字符串 + 认知工具，证明「A 写入的事 B 查不到、A 下拍能 retrieve 到」



### P1 —— 对齐论文反思与规划

- [ ] `reflect` 问题-检索-引用管线；失败可见
- [ ] `plan_compose` / `plan_revise`；计划可被 retrieve
- [ ] importance scorer 可注入
- [ ] 提示词含「importance 累计 / 早晨规划 / 遇见熟人先检索」
- [ ] 关键路径 snapshot 或 keyless 回放：一次「观察→write→retrieve→write plan」transcript



### P2 —— 质量

- [ ] embedding relevance
- [ ] candidate 准入
- [ ] 15 分钟块；反思树可视化仅观测面
- [ ] 更新 [Memory-System-Design.md](./Memory-System-Design.md) §9「引擎集成」：写明 auto/tools 双路径，避免两份文档打架

---



## 7. 建议实施顺序（一条依赖链）

```
R-3 依赖方向写清
  → P0-3 ToolUseContext 注入 manager
  → P0-4 kind=plan + evidence_ids
  → C-1 recency 回写
  → P0-1 认知工具 + 测试
  → C-2 memory_injection
  → P0-2 / R-2 提示词按工具渲染
  → R-1 工具包拆分
  → P0-5 每实例默认路径
  → example 双 agent 隔离
  → P1 reflect / plan 厚工具
  → C-5 Config 旋钮
  → 小镇改为注册工具 + 停用纯 JSON 动作（游戏仓）
```

---



## 8. 风险


| 风险                          | 影响              | 缓解                                            |
| --------------------------- | --------------- | --------------------------------------------- |
| 模型不调用记忆工具                   | 每拍失忆，比今日自动注入更差  | `hybrid` 过渡；提示词写死触发条件；观测 `memory/retrieve` 计数 |
| 每拍 retrieve+reflect 爆 token | 成本回到论文「两日数千美元」  | 厚工具内部限 k；threshold 只提示不自动跑；日常用小模型             |
| `tools` 切默认误伤 CLI           | 编码助手突然「不记得用户偏好」 | 默认保持 `auto`                                   |
| 计划工具与 `enter_plan_mode` 混淆  | 误把 NPC 打进只读权限   | 不给 NPC 注册后组工具；文档去冲突                           |
| 工具写记忆绕过 admit               | 垃圾记忆膨胀          | 工具必须走 `admit()`；低分进 candidate（P2）             |


---



## 9. 一句话

**nono-agent 缺的不是「再做一个小镇引擎」，而是把已经存在的 MemoryManager 从「组 prompt 的隐式副作用」变成模型可调用的原语工具，并让自动注入可关。** 反思与规划在论文里是检索之上的算法，应作为可选厚工具躺在同一条 MemoryManager 上，而不是导演循环或 `todo_write` 的新语义。