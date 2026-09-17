# Nono Agent —— 长期/短期记忆系统功能设计与优化文档

> **文档角色：** 为 `nono_agent/memory/`（及与其协同的 `pipeline/`、`prompt/`、`session/`）定义**长短期记忆的设计方案与落地对照**。已实现部分与代码对齐；未实现部分仍为规划。
> **配套文档：** 本文与《会话存储与可观测性设计》同属一组；两者共同承接《斯坦福小镇 × nono-agent 集成报告》中「记忆是灵魂」与「可观测/可回溯」两条主线。
> **对标参考：** ADPS 记忆模块白皮书（https://adpsagent.com/zh/patterns/memory ）与《Generative Agents》(Park et al., UIST 2023, arXiv:2304.03442)。
> **当前版本基准：** `0.1.0`。P0（envelope + 启发式检索 + 引擎检索注入）已落地；P1 中 `reflect()` API 与 `memory/write`/`memory/retrieve` 事件已落地，embedding 未做；P2 中版本链已落地，候选池治理与群组共享未做。

---

## 1. 结论先行（TL;DR）

1. **短、中、长期记忆必须分层，且各有明确的所有者**：短期 = 进程内 `MessageStore` + 上下文压缩管线；中期 = 会话 `Transcript`（`session/`）；长期 = `memory/`（已从「文件名即记忆」升级为 envelope + `MemoryManager` 编排）。
2. **长期记忆的关键增量不是「存」，而是「准入、检索、反思、版本、可追溯」五件事。** 其中准入门槛、三因子检索、`supersedes` 版本链、会话事件追溯已落地；候选池真正作为写入前置、embedding、`persona` 恒常注入、`last_access_at` 回写尚未做。
3. **检索打分直接复现论文三因子**：`score = α·recency + β·importance + γ·relevance`。当前是启发式（词频/Jaccard + CJK 单字），embedding 仍为 P1。
4. **反思 = 可注入 summarizer 的沉降管线**：`MemoryManager.reflect()` 已能把近期 episodic 归纳为 `meta` 记忆；尚未挂到导演循环或 `/reflect`，也尚未与 `ContextCollapse` 共用同一 summarizer 实例。
5. **记忆的每一条都要能回答**：「谁写的、什么类型、何时有效、从哪来、覆盖了谁、被谁取回过、影响了哪个决策」——即 ADPS 的 memory envelope。字段已齐；「被谁取回过」的计数尚未在检索路径回写。

---

## 2. 现状盘点（代码事实）

对照 `nono_agent/memory/`、`core/engine.py`、`core/config.py`、`commands/builtin.py`、`prompt/sections/memory_section.py`。

### 2.1 已落地

| 资产 | 位置 | 当前能力 |
|------|------|---------|
| `MemoryRecord` envelope | `memory/models.py` | 扁平字段覆盖 id / kind / scope / source_ref / validity / status / ranking / keys / embedding / risk；`Memory` 为历史别名；`ensure_identity()` / `is_active()` |
| `MemoryStore` 协议 | `memory/store.py` | `load()` / `add()` / `update()` / `delete()` / `get()` / `list_candidates()` / `list_all()` |
| `FileSystemMemoryStore` | 同上 | `~/.nono-agent/memory/<memory_id>.json`；按 `created_at` 倒序；旧 `.md` 被 `load()` 忽略 |
| `SQLiteMemoryStore` | 同上 | 单文件 `memories(memory_id, data JSON)`；另有 `retire()` / `accept()` / `close()`；查询在 Python 层，无 FTS5 |
| `MemoryManager` | `memory/manager.py` | `admit()`（重要性门槛 + supersedes 链）、`add_observation()`、`retrieve()`（三因子 + top-k + token 预算）、`reflect()`（需注入 summarizer）、写入/检索观察者 |
| 引擎注入 | `core/engine.py` | 构造时注入 `memory_store` + `memory_manager`（缺省 `FileSystemMemoryStore` + 其上的 `MemoryManager`）；`_build_system_prompt` 按当前 query 做 `retrieve()`，拼 `- [kind] content` |
| `EngineConfig` | `core/config.py` | `agent_id`、`memory_retrieval_k=10`、`memory_retrieval_token_budget=2000` |
| 会话事件 | `engine._register_event_observers` | `admit` → `memory/write`；`retrieve` → `memory/retrieve`（含组装 system prompt 时的自动检索） |
| `/memory` 命令 | `commands/builtin.py` | 列出全部记忆的 `kind` / `status` / content / `agent_id` |

### 2.2 仍缺口（相对本文设计）

| 资产 | 缺口 |
|------|------|
| 候选池治理 | `status=candidate` 与 `list_candidates()` 已有；`admit()` 把「非 accepted 或 importance 过低」直接打成 `rejected`，不经过候选区 |
| 检索回写 | `last_access_at` / `access_count` 字段在，`retrieve()` 不更新 |
| 分层注入 | `persona` 不恒常注入；`procedural` 无触发器；全部 kind 走同一套 top-k 预算 |
| `MemorySection` | 仍只包 `<memory>…</memory>`，不感知命中来源 |
| `/memory` | 仅列表，无检索 / 管理 / 溯源；无 `/reflect` |
| SQLite | JSON blob，无结构化列、无 FTS5；引擎默认仍是文件系统后端 |
| 反思调度 | `reflect()` 可调用，无积累 N 条自动触发、无导演循环 / slash 挂点 |
| embedding | 字段保留，打分未用 |
| 编译管线 | `source_ref` 可手填，无「摘要 + 实体 + 原文指针」自动编译 |

**结论：** 长期记忆已从文件工具升级为记忆子系统的 P0 骨架（模型 + 双后端 + 编排 + 引擎检索注入 + 事件）。剩余工作集中在准入治理、分层注入、反思调度、语义检索与 `/memory` 管理面。

---

## 3. 分层记忆模型（短 / 中 / 长期）

参照 ADPS「分层保留（M1）」与张栋「短/中/长期漏斗」的共识，明确三层归属与读写策略：

| 层 | 名称 | 对应 ADPS kind | 所有者组件 | 生命周期 | 读写 |
|----|------|---------------|-----------|---------|------|
| L0 短期（工作记忆） | 当前上下文窗口 | `working` | `MessageStore` + `PreprocessPipeline` | 单次 `submit_message` 到窗口上限 | 每次 LLM 调用前压缩；不可直接检索 |
| L1 中期（情景记忆/会话级） | 单会话完整事件流 | `episodic`（原始事件） | `session/` Transcript | 会话存续；可 resume/fork/replay | append-only；按 seq 检索 |
| L2 长期（跨会话） | 记忆流 + 反思产物 | `semantic` / `episodic` / `procedural` / `meta` | `memory/` | 跨会话持久 | 准入写、按需检索、版本化更新 |

> **关键边界（ADPS 三条工程边界在本项目的映射）：**
> - **知识库 ≠ 记忆**：小镇的世界规则、NPC 性格设定、领域文档是「知识」，走 `rules/`/`skills/`/外部知识库；记忆只存「**这个 agent 亲历/判断/学到**」的经验与事实。
> - **数据本体 ≠ 记忆**：世界状态（位置、对象、时间）是权威业务事实，归 `TownWorld`/控制平面；记忆只存「围绕这些对象**发生过什么、做过什么判断**」，并持有指向权威状态的引用。
> - **控制平面 ≠ 记忆**：NPC 当前 plan、审批状态、世界时钟是权威状态；记忆的「进度叙事」只引用它，不替代它。
> - 三者可共用基础设施（如共用 SQLite/向量库），但**所有权不混**。

### 3.1 长期记忆的 `kind` 枚举（对齐 ADPS）

`VALID_KINDS` 已包含下表全部取值；引擎检索目前**不**按 kind 分层注入（见 §6 缺口）。

| kind | 含义 | 小镇示例 |
|------|------|---------|
| `semantic` | 稳定的、跨场景的事实/偏好/自我认知 | 「我（Isabella）喜欢在咖啡馆写作」 |
| `episodic` | 具体时间点的经历/事件高亮 | 「昨天下午 Sam 告诉我咖啡馆要涨价」 |
| `procedural` | 已验证的、可复用的做法/技能（M5） | 「去图书馆查资料要先找管理员登记」 |
| `meta` | 关于记忆/自我模型的高层认知（反思产物） | 「我发现自己总是回避与 Klaus 的争论」 |
| `persona` | 角色恒常约束（性格/背景/口头禅） | persona 设定，**设计上恒常注入、不进检索预算**（尚未实现） |

> `persona` 是小镇场景特有的「常驻 meta 记忆」，与 `custom_system_prompt` 的角色段区分：persona 走记忆系统（可版本化、可溯源），system prompt 只做一次性注入的兜底。

---

## 4. 记忆记录模型（Memory Envelope）

把 `Memory` 从 4 字段升级为 ADPS envelope。**正文只是记忆的一部分**，元数据决定系统能否稳定处理冲突、权限、回滚与删除。

代码实现是**扁平 dataclass**（`MemoryRecord`），下表嵌套只作概念分组；`Memory` 仍是别名。

```
MemoryRecord {                          # nono_agent/memory/models.py
  memory_id: str                        # mem_<12 hex>；add() 时 ensure_identity()
  name: str                             # 可选人类可读标签（历史兼容）
  content: str                          # 正文（摘要/结论；用于注入与检索）
  kind: str                             # working|episodic|semantic|procedural|meta|persona
                                        # 默认 semantic
  type: str | None                      # 历史兼容（如 preference / fact）
  source: str                           # 历史兼容的来源标签（observation / reflection / …）

  # scope
  agent_id / session_id / project_id: str | None   # None=全局 / 未绑定

  # source
  source_ref: str | None                # 原文指针：trace://session/<sid>/seq/<n>

  # validity
  valid_from / valid_to: float | None   # epoch 秒；None valid_to = 永久
  supersedes / superseded_by: str | None

  # trust
  status: candidate|accepted|rejected|retired     # 默认 accepted
  reviewed_by: str                      # 默认 policy://memory-admission

  # ranking
  importance: float                     # 1–10，默认 5.0
  created_at / last_access_at: float    # epoch 秒
  access_count: int

  # retrieval
  keys: list[str]
  embedding: list[float] | None         # 字段已有，打分未用
  risk: low|medium|high                 # 默认 low
}
```

`is_active()`：`status==accepted` 且当前时间落在 `[valid_from, valid_to]`。`retrieve()` 只取 active 记录。

> **版本链（对齐陈玉涛）**：新事实**不静默覆盖**旧事实。`admit()` 若带 `supersedes`，会把旧记录置 `retired` 并回填 `superseded_by`。是否遗忘由场景决定（高噪声观测淘汰，连续关系/项目历程长存）。

---

## 5. 记忆生命周期管线（候选 → 准入 → 编译 → 检索 → 追溯 → 版本）

这是 ADPS 总纲最核心、也最容易被省略的部分。落地为六段（括号内为当前状态）：

```
观察/工具事件/对话/反思
        │
   [1] 候选池 candidate pool ────────────── 字段与 list_candidates() 已有；admit() 未走此段
        │
   [2] 准入 admission ── ✅ importance 门槛 + supersedes；敏感信息 / 冲突 / 风险分级未做
        │
   [3] 编译 compilation ── source_ref 可手填；自动摘要/实体未做
        │
   [4] 检索 retrieval ── ✅ 三因子 + top-k + token 预算 + agent 隔离；persona 恒常注入未做
        │
   [5] 使用追溯 trace ── ✅ memory/write + memory/retrieve 进事件流；last_access_* 未回写
        │
   [6] 版本更新 version ── ✅ admit() 维护 supersedes 链；delete() 仍是硬删
```

- **[1] 候选池（未按设计走）**：会话摘要、工具事件、模型反思**设计上**先进入候选区（状态 `candidate`），不能直接进入活动记忆。当前 `add_observation()` 直接以 `accepted` 调用 `admit()`；`admit()` 对非 `accepted` 或 `importance < min_importance`（默认 3.0）一律打成 `rejected`。
- **[2] 准入（部分落地）**：重要性门槛与 supersedes 链已在 `MemoryManager.admit()`。来源 / 敏感信息 / 冲突 / 风险审核尚未做。
- **[3] 编译（未做）**：写侧「知识编译」（摘要 + 实体 + 原文指针）仍靠调用方填 `content` / `keys` / `source_ref`。
- **[4] 检索（已落地）**：见 §6。`retrieve()` 在 `MemoryManager` 上，不在 `MemoryStore` 协议里。
- **[5] 追溯（部分落地）**：观察者把每次写入/检索追加为 `memory/write`、`memory/retrieve`（载荷见《会话事件目录》§2.4）。`last_access_at` / `access_count` 未在检索路径更新。`adopted` 对 write 表示 `status==accepted`，对 retrieve 表示是否有命中，**不是**「模型是否采用」。
- **[6] 版本（部分落地）**：`admit(supersedes=…)` 维护链。协议 `delete()` 是硬删；仅 `SQLiteMemoryStore.retire()` 软删。

---

## 6. 检索打分（Generative Agents 三因子）

已落地：`MemoryManager.retrieve(query, *, agent_id, k, token_budget, kinds, now) -> list[MemoryRecord]`

```
score(m) = α · recency(m) + β · importance(m) + γ · relevance(m, query)
```

| 因子 | 计算 | 备注 |
|------|------|------|
| `recency` | 指数衰减 `e^{-Δt/λ}`，Δt = `now - created_at` | 默认 λ=`86400` 秒（1 天），构造 `MemoryManager` 时可改 |
| `importance` | `importance / 10` 归一化到 [0,1] | 写入时由调用方给出；无 LLM 自动打分 |
| `relevance` | Jaccard：query token ∩ (`content` token ∪ `keys`) | 英文单词 + CJK 单字；无 embedding |

实现要点（与代码一致）：

- **归一化**：三因子各自在 [0,1]，默认 α=0.3, β=0.4, γ=0.3（`MemoryManager` 构造参数，**未**进 `EngineConfig`）。
- **过滤**：只取 `is_active()`；`agent_id` 指定时只保留 `m.agent_id in (None, agent_id)`（全局记忆可被任意 agent 命中）；可选 `kinds`。
- **预算封顶**：`top-k`（默认 10，`EngineConfig.memory_retrieval_k`）+ `token_budget`（默认 2000，粗估 `len(content)//4`）双重上限。第一条不受预算卡死，避免空结果。
- **引擎注入**：`_build_system_prompt(query)` 用当前用户话做 query，结果渲染为 `- [kind] content` 再交给 `MemorySection`。
- **冷启动**：无 embedding，纯启发式（已跑通，零额外成本）。

尚未落地：

- **分层注入**：`persona`/`meta` 恒常注入（小）；`semantic/episodic` 走检索；`procedural` 走触发器。
- **检索回写** `last_access_at` / `access_count`。
- **P1 embedding 余弦** 替换/增强 relevance。

---

## 7. 反思（Reflection）—— 长期记忆的「沉降」

`MemoryManager.reflect(*, agent_id, summarizer, model_id, limit=20)` **已落地**，行为：

- `summarizer is None` 或无匹配 episodic → 返回 `[]`。
- 取该 `agent_id` 下 active 的 `episodic`，按 `created_at` 取最近 `limit` 条，交给 summarizer。
- 成功则 `admit()` 一条 `kind=meta`、`importance=8.0`、`keys=["reflection"]`、`source="reflection"` 的记忆。
- summarizer 抛错或空摘要 → 返回 `[]`（不落库）。

与设计仍差一截：

- **触发**：设计为记忆流累计 N 条或游戏时间到整夜/整日。当前无自动触发、无 `/reflect`、无导演循环挂点；调用方自行 `await engine.memory_manager.reflect(summarizer=…)`。
- **与压缩的差异**（设计仍成立）：`ContextCollapse` 的产物是「**上下文摘要**」（短期、随会话丢弃）；反思的产物是「**长期认知**」（持久、可检索）。两者**尚未**共用同一个 summarizer 实例。
- **成本**：反思是一次强模型调用；摊薄策略（积累 N 条）未接线。`METRIC_MEMORY_REFLECT` 常量已声明，事件投影只接了 `memory/write` 与 `memory/retrieve`。

> 一句话：**压缩解决「窗口装不下」，反思解决「跨窗口记得住」。**

---

## 8. 存储后端（Protocol 演进）

保持 `MemoryStore` 为 Protocol（引擎只认协议，不认实现）。**检索与反思在 `MemoryManager`，不在 Store。**

当前协议（与 `store.py` 一致）：

```
class MemoryStore(Protocol):
    def load(self) -> list[MemoryRecord]: ...              # 含各 status；调用方过滤
    def add(self, record: MemoryRecord) -> MemoryRecord: ...  # 按 memory_id 覆写文件/行
    def update(self, memory_id, **patch) -> MemoryRecord | None: ...
    def delete(self, memory_id) -> bool: ...               # 硬删
    def get(self, memory_id) -> MemoryRecord | None: ...
    def list_candidates(self) -> list[MemoryRecord]: ...
    def list_all(self) -> list[MemoryRecord]: ...          # 与 load() 同义（不滤 retired）
```

`SQLiteMemoryStore` 额外提供 `retire()` / `accept()` / `close()`，未进 Protocol。

| 后端 | 定位 | 说明 |
|------|------|------|
| `FileSystemMemoryStore`（已落地） | 引擎默认 | `~/.nono-agent/memory/<memory_id>.json` 存完整 envelope；不再按文件名/同名 `.md` 覆写；无独立索引文件 |
| `SQLiteMemoryStore`（已落地） | 可注入 | 单文件 JSON blob 表；无结构化列、无 FTS5；适合测试与单机注入 |
| `VectorMemoryStore`（P1，未做） | 语义检索 | 在 SQLite 外加 embedding 列，或 sqlite-vec/Chroma；只替换 `relevance` 因子 |

> 与 `session/` 的边界：`session/` 存「**原始事件流**」（append-only、可重放），`memory/` 存「**从事件流提炼的可检索资产**」。记忆的 `source_ref` 指向 session 事件的 `seq`，形成「记忆 → 原文」的可回溯链路（字段已有，写入方需填写）。

---

## 9. 引擎集成点（最小侵入）

1. **`EngineConfig`（已落地子集）**：`agent_id`、`memory_retrieval_k`、`memory_retrieval_token_budget`。**未**做 `memory_backend`、α/β/γ、`reflection_trigger_n`；打分权重只在 `MemoryManager` 构造参数。
2. **`_build_system_prompt`（已改检索注入）**：`MemoryManager.retrieve(query=当前用户话, agent_id, k, token_budget)` 取 top-k 拼串。`persona` 恒常注入未做。
3. **`MemorySection.render`（未改）**：仍消费 `PromptContext.memories_text` 裸文本，无命中来源标注。
4. **`QueryEngine` 注入 `MemoryManager`（已落地）**：与 `memory_store` 并列；缺省 `FileSystemMemoryStore()` + `MemoryManager(store)`。测试/小镇可注入 `SQLiteMemoryStore`。
5. **反思挂点（API 已有、调度未接）**：引擎暴露 `engine.memory_manager`；导演循环与 `/reflect` 尚未调用 `reflect()`。
6. **事件观察者（已落地）**：`_register_event_observers` 把 write/retrieve 接到 `_stage_event`，与权限拒绝同一机制。

> 依赖方向（遵守 ROADMAP §5.3）：`core → memory`（消费协议）；`prompt → memory 的已加载视图`（只读）；`memory ↛ tools 执行实现`。**不反向耦合**。

---

## 10. 与 Smallville 的映射（验收口径）

| 报告要求 | 本设计落点 | 状态 |
|---------|-----------|------|
| MemoryStream（带时间戳事件日志） | `MemoryRecord` + `agent_id` + `created_at`，按 agent 过滤 | ✅ 字段与检索过滤 |
| 检索打分 recency/importance/relevance | §6 `MemoryManager.retrieve` | ✅ 启发式；embedding 未做 |
| 反思（定期归纳高层认知并回写） | §7 `MemoryManager.reflect` | ✅ API；❌ 定期触发 |
| 角色一致性 / 不断片 | `persona` 恒常注入 + `semantic/meta` 检索 + 反思沉降 | ⚠️ 检索+反思可用；persona 恒常注入未做 |
| 每次决策回合 = 检索→判断→动作 | 组 prompt 时 `retrieve()`；动作后写 observation 候选 | ⚠️ 检索已挂引擎；观察写入靠调用方 `add_observation()` |

---

## 11. 分阶段落地

| 阶段 | 内容 | 验收 | 状态 |
|------|------|------|------|
| **P0**（对应报告 P1） | 升级 `Memory`→envelope；`SQLiteMemoryStore`；启发式 `retrieve`；引擎改「检索注入」 | NPC「记得昨天跟谁聊过」并在新对话体现 | ✅ 代码与 `tests/test_memory*.py` |
| **P1** | 反思 `reflect()` 落库；embedding relevance；`memory/retrieve` 事件接入 session | 「反思」产物可检索；语义相似旧事可命中 | ⚠️ `reflect()` + 事件已做；embedding 未做 |
| **P2** | 版本链 + `candidate` 准入治理 + `procedural` 记忆 + 群组记忆共享 | 事实更新可回滚；跨 NPC 信息传播可控制 | ⚠️ `supersedes` 链已做；候选治理 / procedural 触发 / 群组共享未做 |

---

## 12. 多维合理性校验

### 12.1 技术可行性 —— ✅ 高
- `MemoryStore` Protocol、`MemoryManager`、`MemorySection`、`_build_system_prompt` 检索注入、可注入 summarizer 均已在位。后续是准入治理与分层注入，不推翻引擎。
- 检索启发式无需 embedding 即可跑通（对齐报告 §6.5 结论）。

### 12.2 成本/性能 —— ✅ 可控
- 全量 load → top-k 检索，**记忆越多反而越省**（prompt 恒有预算封顶）。当前实现每次 `retrieve()` 仍 `store.load()` 全量再在内存打分，量级到「单机几十 NPC」可接受，再往上需要 Store 侧过滤/FTS。
- 反思摊薄（积累 N 条才一次强模型调用）尚未接线。
- SQLite FTS/索引仍是规划；embedding 仍是可选增强。

### 12.3 一致性/正确性 —— ✅ 强（版本链已接）
- `admit(supersedes=…)` 杜绝静默覆写同事实；`supersedes/superseded_by` 支持回滚与「时点事实」查询。
- `candidate` 候选池防止低质量内容直接污染活动记忆——**设计仍成立，admit 路径尚未使用该状态**。

### 12.4 安全/隐私 —— ⚠️ 需显式处理
- 敏感信息标记 `risk` 字段已有；准入阶段过滤密钥/PII、高风险复现/人工审核尚未做。
- `agent_id` 隔离已在 `retrieve()`：指定 agent 时看不到其他 agent 的私有记忆；`agent_id is None` 的全局记忆仍可见。

### 12.5 可观测性 —— ✅ 事件已接（计数未回写）
- `source_ref` 可指向 session 事件 `seq`（需写入方填写）。
- `memory/retrieve` / `memory/write` 已进事件流，指标投影为 `nono_agent.memory.retrieve` / `nono_agent.memory.write`（见《会话存储与可观测性设计》）。
- 记录级 `last_access_at` / `access_count` 尚未随检索更新。

### 12.6 可演进性 —— ✅ 强
- Protocol 优先：`FileSystemMemoryStore` / `SQLiteMemoryStore` 可插拔；`MemoryManager` 是编排层。
- 遵守 ROADMAP 依赖方向，`memory` 不反向 import 工具/适配器。

### 12.7 与 ADPS 对照自检
| ADPS 判据 | 本项目结论 |
|-----------|-----------|
| 「是否每个 agent 都需要记忆服务？」 | 小镇 NPC **是**（偏好与经验动态变化、使用者不会手工维护）；通用 CLI 单轮任务可保持无状态 |
| 「稳定偏好可手工维护？」 | persona/性格走文件 + 版本化规则；动态经验走记忆服务（二者边界在 §3） |
| 「知识库/数据本体/控制平面分权」 | §3 边界表已明确，不混所有权 |
| 「envelope 字段完备」 | §4 覆盖 id/kind/scope/source/validity/trust/retrieval（扁平字段） |
| 「五模式分工」 | M1 分层=§3；M2 RAG=§6 relevance；M3 进度=plan 引用控制平面（小镇 director 侧）；M4 失败日记=`reflection`+`status=rejected` 反例库；M5 程序性=§3 `procedural` kind |

### 12.8 风险与预案
| 风险 | 影响 | 预案 |
|------|------|------|
| 检索质量差导致「断片」 | 前后矛盾 | 先三因子启发式（已做），后 embedding；加反思层；保留 `persona` 恒常兜底（未做） |
| 反思产物质量低/漂移 | 自我认知失真 | 反思产物先 `candidate`，准入后再 `accepted`（当前 reflect 直接 accepted）；限制反思频率 |
| 记忆膨胀 | 检索变慢、噪音 | 准入淘汰 + `access_count` 冷启动降权（计数未回写）+ 归档 `retired` |
| 记忆污染跨 NPC 扩散 | 群体错误经验 | `agent_id` 隔离默认开；群组共享走传播控制（P2） |
| 版本链过长 | 查询/存储开销 | 定期把「已取代且冷」的记忆物理归档 |

---

## 附：关键改动点速查

| 文件 | 状态 | 说明 |
|------|------|------|
| `nono_agent/memory/models.py` | ✅ | `MemoryRecord` envelope；`Memory` 别名 |
| `nono_agent/memory/store.py` | ✅ | Protocol 扩展 + FileSystem JSON + SQLite blob；无 FTS5 |
| `nono_agent/memory/manager.py` | ✅ | 准入 / 检索 / 反思 / 观察者 |
| `nono_agent/core/engine.py` | ✅ | 注入 `MemoryManager`；检索注入；事件观察者 |
| `nono_agent/core/config.py` | ✅ 子集 | `agent_id` + k + token_budget；无 backend / 权重 / 反思阈值 |
| `nono_agent/prompt/sections/memory_section.py` | 未改 | 仍渲染 `memories_text` |
| `nono_agent/commands/builtin.py` | 部分 | `/memory` 展示 kind/status/agent；无检索/管理/溯源 |
| `nono_agent/pipeline/context_collapse.py` | 未改 | 与反思共用 summarizer 仍是规划 |
