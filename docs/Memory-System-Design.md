# Nono Agent —— 长期/短期记忆系统功能设计与优化文档

> **文档角色：** 为 `nono_agent/memory/`（及与其协同的 `pipeline/`、`prompt/`、`session/`）定义**长短期记忆的完整设计方案**。本文只做设计规划，不落代码。
> **配套文档：** 本文与《会话存储与可观测性设计》同属一组；两者共同承接《斯坦福小镇 × nono-agent 集成报告》中「记忆是灵魂」与「可观测/可回溯」两条主线。
> **对标参考：** ADPS 记忆模块白皮书（https://adpsagent.com/zh/patterns/memory ）与《Generative Agents》(Park et al., UIST 2023, arXiv:2304.03442)。
> **当前版本基准：** `0.1.0`；`memory/` 处于「P2 骨架」状态。

---

## 1. 结论先行（TL;DR）

1. **短、中、长期记忆必须分层，且各有明确的所有者**：短期 = 进程内 `MessageStore` + 上下文压缩管线（已存在，需微调）；中期 = 会话 `Transcript`（`session/`，已存在）；长期 = `memory/`（**需从「文件名即记忆」升级为「可治理的记忆流 + 检索 + 反思」**）。
2. **长期记忆的关键增量不是「存」，而是「准入、检索、反思、版本、可追溯」五件事**。这正是当前 `FileSystemMemoryStore` 完全没有的。
3. **检索打分直接复现论文三因子**：`score = α·recency + β·importance + γ·relevance`，先做启发式（词频/Jaccard），再升级 embedding。
4. **反思 = 复用 `ContextCollapse` 的 summarizer 基础设施**，把「压缩上下文」与「归纳高层认知」统一成一条「沉降管线」。
5. **记忆的每一条都要能回答**：「谁写的、什么类型、何时有效、从哪来、覆盖了谁、被谁取回过、影响了哪个决策」——即 ADPS 的 memory envelope。

---

## 2. 现状盘点（代码事实）

| 现有资产 | 位置 | 能力 | 缺口 |
|---------|------|------|------|
| `Memory` 数据模型 | `nono_agent/memory/models.py` | 仅 `name/content/type/source` | 无时间戳、无重要性、无 `kind`、无有效时间、无版本、无来源引用、无 trace |
| `MemoryStore` 协议 | `nono_agent/memory/store.py` | `load()/add()/delete()` | 无检索 `retrieve()`、无 `query()`、无 `reflect()`、无 `update/version` |
| `FileSystemMemoryStore` | 同上 | memdir 下每 `.md` 一条，frontmatter 可带 `type` | 全量 `load()` 无排序无打分；`add()` 同名静默覆写 |
| 记忆注入 | `core/engine.py::_build_system_prompt`（596–599 行） | 把 `load()` 的**全部**记忆拼成 `memories_text` | 无预算、无检索、无排序，记忆一多就撑爆上下文 |
| `MemorySection` | `prompt/sections/memory_section.py` | 把 `memories_text` 包成 `<memory>…</memory>` | 只渲染文本，不感知「哪条被取回/命中」 |
| `/memory` 命令 | `commands/builtin.py`（183 行） | 列出全部记忆 | 仅列表，无检索/管理/溯源 |
| 短期记忆（上下文） | `session/store.py::MessageStore` + `pipeline/` | 单一数据源 + 4 级压缩 + 可选 `ContextCollapse` | 已有，但「压缩产物」与「长期记忆」之间没有沉降通道 |
| 反思/摘要 | `pipeline/context_collapse.py` + `summarizer.py` | LLM 摘要旧消息 → `compact_boundary` + 摘要消息 | 摘要**只回上下文、不回长期记忆**，即「只压缩、不沉淀」 |

**结论：** 长期记忆是唯一「近空白」的板块。设计重心应放在**把 `memory/` 从文件工具升级为记忆子系统**，并打通它与 `pipeline/`（反思）、`prompt/`（注入）、`session/`（来源引用）的接口。

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

| kind | 含义 | 小镇示例 |
|------|------|---------|
| `semantic` | 稳定的、跨场景的事实/偏好/自我认知 | 「我（Isabella）喜欢在咖啡馆写作」 |
| `episodic` | 具体时间点的经历/事件高亮 | 「昨天下午 Sam 告诉我咖啡馆要涨价」 |
| `procedural` | 已验证的、可复用的做法/技能（M5） | 「去图书馆查资料要先找管理员登记」 |
| `meta` | 关于记忆/自我模型的高层认知（反思产物） | 「我发现自己总是回避与 Klaus 的争论」 |
| `persona` | 角色恒常约束（性格/背景/口头禅） | persona 设定，**恒常注入，不进检索预算** |

> `persona` 是小镇场景特有的「常驻 meta 记忆」，与 `custom_system_prompt` 的角色段区分：persona 走记忆系统（可版本化、可溯源），system prompt 只做一次性注入的兜底。

---

## 4. 记忆记录模型（Memory Envelope）

把 `Memory` 从 4 字段升级为 ADPS envelope。**正文只是记忆的一部分**，元数据决定系统能否稳定处理冲突、权限、回滚与删除。

```
MemoryRecord {
  memory_id: str                 # 稳定唯一 id（mem_<uuid>）
  kind: working|episodic|semantic|procedural|meta|persona
  content: str                   # 正文（摘要/结论；用于注入与检索）

  scope: {
    agent_id: str | null         # 所属 NPC / 用户（null=全局）
    session_id: str | null       # 来源会话
    project_id: str | null       # 项目/租户（小镇=一个 world）
  }

  source: {
    type: "tool_event" | "observation" | "conversation" | "reflection"
        | "user_instruction" | "procedural_certification"
    ref: str                     # 原文指针：trace://session/<sid>/seq/<n> 或 transcript 消息 uuid
  }

  validity: {
    valid_from: timestamp
    valid_to: timestamp | null
    supersedes: str | null       # 被本记忆取代的 memory_id（版本链）
    superseded_by: str | null
  }

  trust: {
    status: "candidate" | "accepted" | "rejected" | "retired"
    reviewed_by: str             # policy://memory-admission / model / human
  }

  ranking: {                     # 检索打分三因子 + 统计
    importance: float            # 1–10，写入时由 LLM 或启发式评估
    created_at: timestamp
    last_access_at: timestamp
    access_count: int
  }

  retrieval: {
    keys: list[str]              # 关键词/实体标签，供词频与 FTS
    embedding: list[float] | null  # P1 语义检索
    risk: "low"|"medium"|"high"  # 敏感/高风险内容标记
  }
}
```

> **版本链（对齐陈玉涛）**：新事实**不静默覆盖**旧事实，而是保留 `valid_from` + `supersedes/superseded_by`，使系统能回答「某一时点记忆是什么」、能比较前后变化、能回滚。是否遗忘由场景决定（高噪声观测淘汰，连续关系/项目历程长存）。

---

## 5. 记忆生命周期管线（候选 → 准入 → 编译 → 检索 → 追溯 → 版本）

这是 ADPS 总纲最核心、也最容易被省略的部分。落地为六段：

```
观察/工具事件/对话/反思
        │
   [1] 候选池 candidate pool ────────────── 不直接污染活动记忆
        │
   [2] 准入 admission ── 重要性阈值 + 去重 + 冲突检测 + 敏感信息 + 风险分级
        │  （高风险 → 复现或人工审核；低价值 → rejected/直接淘汰）
        │
   [3] 编译 compilation ── 摘要/实体/事件 + 原文指针 ref（结论仍回原文）
        │
   [4] 检索 retrieval ── 目标/预算/权限/有效时间/风险 → 挑 top-k 进 context
        │
   [5] 使用追溯 trace ── 哪条被取回、是否被采用、影响哪个决策（写回 session 事件）
        │
   [6] 版本更新 version ── supersedes 链 + 有效时间，避免静默删除
```

- **[1] 候选池**：会话摘要、工具事件、模型反思**先进入候选区**（状态 `candidate`），不能直接进入活动记忆。小镇里每次「新观察」默认是候选。
- **[2] 准入**：检查来源、作用域、敏感信息、冲突、风险；**重要性分**在此判定（LLM 一次性打分或规则：涉及关系/计划/情绪/承诺的观察重要性高）。高风险内容需复现或人工审核。
- **[3] 编译**：写侧做「知识编译」（对齐张颖峰）——把原始材料转成「摘要 + 实体 + 事件 + 原文指针」，供全文/向量/关联查询；`ref` 永远指向原文，避免摘要失真。
- **[4] 检索**：按任务装配，见 §6。
- **[5] 追溯**：每次检索结果记录 `last_access_at/access_count`，并**向 session 事件流追加 `memory/retrieve` 事件**（含命中 memory_id 与是否被模型采用），与可观测性文档闭环。
- **[6] 版本**：`supersedes` 链 + `retired` 状态，不静默删。

---

## 6. 检索打分（Generative Agents 三因子）

`MemoryManager.retrieve(query, *, agent_id, k, token_budget) -> list[MemoryRecord]`

```
score(m) = α · recency(m) + β · importance(m) + γ · relevance(m, query)
```

| 因子 | 计算 | 备注 |
|------|------|------|
| `recency` | 指数衰减 `e^{-Δt/λ}`（λ 可配，如半天/一天） | 时间越近权重越高 |
| `importance` | 写入时 LLM 打 1–10（归一化到 [0,1]），或规则启发 | 论文用「问 LLM 这条有多重要」，可降级为规则 |
| `relevance` | P0：关键词/Jaccard/FTS 打分；P1：embedding 余弦 | P1 显著提升「记得相关旧事」 |

实现要点：

- **归一化**：三因子各自归一化到 [0,1]，α+β+γ=1（默认 α=0.3, β=0.4, γ=0.3，可配）。
- **预算封顶**：`top-k`（默认 8–12）+ `token_budget`（默认 1500–2000 token）双重上限，**只把高分者注入 prompt**，替代当前「全量 load」。
- **分层注入**：`persona`/`meta` 恒常注入（小）；`semantic/episodic` 走检索（有预算）；`procedural` 走触发器（命中即注入）。
- **冷启动**：无 embedding 时退化为纯启发式（可跑通，零额外成本）。

---

## 7. 反思（Reflection）—— 长期记忆的「沉降」

反思不是独立子系统，而是**复用 `ContextCollapse`/`summarizer` 的「沉降管线」**：

- **触发**：记忆流累计 N 条（默认 20–30）或游戏时间到整夜/整日（导演循环触发）。
- **动作**：取最近一批「未反思」的 episodic 记忆，问 LLM「从这些经历里，你对自己的性格/关系/目标学到了什么」，产出**更高层**的 `meta`/`semantic` 记忆（重要性格子高），回写记忆流。
- **与压缩的差异**：`ContextCollapse` 的产物是「**上下文摘要**」（短期、随会话丢弃）；反思的产物是「**长期认知**」（持久、可检索）。两者共用 summarizer，但**落点不同**（前者进 MessageStore，后者进 MemoryStore）。
- **成本**：反思是一次强模型调用，按「积累 N 条才触发」摊薄，且可选便宜模型。

> 一句话：**压缩解决「窗口装不下」，反思解决「跨窗口记得住」。**

---

## 8. 存储后端（Protocol 演进）

保持 `MemoryStore` 为 Protocol（引擎只认协议，不认实现），扩展方法签名：

```
class MemoryStore(Protocol):
    def load(self) -> list[MemoryRecord]: ...
    def add(self, record: MemoryRecord) -> MemoryRecord: ...
    def update(self, memory_id, patch) -> MemoryRecord: ...   # 版本化更新，非静默覆写
    def delete(self, memory_id) -> bool: ...                  # 置 retired，可软删
    def retrieve(self, query, *, agent_id, k, token_budget) -> list[MemoryRecord]: ...
    def get(self, memory_id) -> MemoryRecord | None: ...
    def list_candidates(self) -> list[MemoryRecord]: ...      # 准入用
```

| 后端 | 定位 | 说明 |
|------|------|------|
| `FileSystemMemoryStore`（演进） | 单机起步 | 保持 memdir 目录，但每条改存 JSON/JSONL（含完整 envelope）+ 一个轻量索引文件；`add` 不再同名覆写 |
| `SQLiteMemoryStore` | **推荐默认** | 单文件，`memories` 表（含 `kind/agent_id/importance/created_at/valid_from/supersedes/status` 等列）+ FTS5 虚拟表做 `relevance`；检索/溯源/版本都用 SQL |
| `VectorMemoryStore`（P1） | 语义检索 | 复用 `SQLiteMemoryStore` 之外加 embedding 列，或用 sqlite-vec/Chroma；只替换 `relevance` 因子实现 |

> 与 `session/` 的边界：`session/` 存「**原始事件流**」（append-only、可重放），`memory/` 存「**从事件流提炼的可检索资产**」。记忆的 `source.ref` 指向 session 事件的 `seq`，形成「记忆 → 原文」的可回溯链路。

---

## 9. 引擎集成点（最小侵入）

1. **`EngineConfig` 增补（可选，向后兼容）**：`memory_backend: "sqlite"|"file"`、`memory_retrieval: {k, token_budget, α/β/γ}`、`reflection_trigger_n`。全部有默认值，不影响现有调用方。
2. **`_build_system_prompt`（596–599 行）改造**：把「全量 `load()` 拼串」改为「`MemoryManager.retrieve(query=最近观察, ...)` 取 top-k 拼串」，`persona` 恒常注入。
3. **`MemorySection.render`**：消费「检索结果」而非裸文本，渲染时附带「命中来源」的轻量标注（供调试，不注入 LLM）。
4. **`QueryEngine` 注入 `MemoryManager`**：与现有 `memory_store` 并列，默认由 `FileSystemMemoryStore` 构造；CLI/Server 可注入 SQLite/向量实现。
5. **反思挂点**：在导演循环（小镇）或 `/reflect` 命令（通用）触发；引擎提供 `MemoryManager.reflect()` 供外部调度。

> 依赖方向（遵守 ROADMAP §5.3）：`core → memory`（消费协议）；`prompt → memory 的已加载视图`（只读）；`memory ↛ tools 执行实现`。**不反向耦合**。

---

## 10. 与 Smallville 的映射（验收口径）

| 报告要求 | 本设计落点 |
|---------|-----------|
| MemoryStream（带时间戳事件日志） | `MemoryRecord` + `scope/agent_id` + `ranking.created_at`，`SQLiteMemoryStore` 一 agent 一作用域 |
| 检索打分 recency/importance/relevance | §6 `MemoryManager.retrieve` |
| 反思（定期归纳高层认知并回写） | §7 `MemoryManager.reflect`（复用 summarizer） |
| 角色一致性 / 不断片 | `persona` 恒常注入 + `semantic/meta` 检索 + 反思沉降 |
| 每次决策回合 = 检索→判断→动作 | 导演循环组装观察时调用 `retrieve()`，动作落库后写 `observation` 候选记忆 |

---

## 11. 分阶段落地

| 阶段 | 内容 | 验收 |
|------|------|------|
| **P0**（对应报告 P1） | 升级 `Memory`→envelope；`SQLiteMemoryStore`；启发式 `retrieve`（recency+importance+词频 relevance）；引擎改「检索注入」 | NPC「记得昨天跟谁聊过」并在新对话体现；行为矛盾明显下降 |
| **P1** | 反思 `reflect()` 落库；embedding relevance；`memory/retrieve` 事件接入 session | 「反思」产物可检索；语义相似旧事可命中 |
| **P2** | 版本链 + `candidate` 准入治理 + `procedural` 记忆 + 群组记忆共享（多 agent 读同一记忆，含传播控制） | 事实更新可回滚；跨 NPC 信息传播可控制 |

---

## 12. 多维合理性校验

### 12.1 技术可行性 —— ✅ 高
- 现有 `MemoryStore` Protocol、`MemorySection`、`_build_system_prompt` 注入点、`summarizer` 均已在位，改造是「扩展字段 + 加检索 + 加落点」，不推翻引擎。
- 检索启发式无需 embedding 即可跑通（对齐报告 §6.5 结论）。

### 12.2 成本/性能 —— ✅ 可控
- 全量 load → top-k 检索，**记忆越多反而越省**（prompt 恒有预算封顶）。
- 反思摊薄（积累 N 条才一次强模型调用）；日常动作可用便宜模型。
- SQLite FTS/索引满足单机几十 NPC 的量级；embedding 是可选增强。

### 12.3 一致性/正确性 —— ✅ 强
- 版本链杜绝静默覆写；`supersedes/superseded_by` 支持回滚与「时点事实」查询。
- `candidate` 候选池防止低质量内容直接污染活动记忆。

### 12.4 安全/隐私 —— ⚠️ 需显式处理
- 敏感信息标记 `retrieval.risk`；准入阶段过滤密钥/PII；`scope` 隔离（NPC 间默认不共享）。
- 高风险记忆需复现或人工审核（对齐 ADPS）。

### 12.5 可观测性 —— ✅ 由配套文档保证
- 每条记忆有 `source.ref` 指向 session 事件 `seq`；`memory/retrieve`/`memory/write` 事件进事件流，形成「记忆→决策」全链路（见《会话存储与可观测性设计》）。

### 12.6 可演进性 —— ✅ 强
- Protocol 优先：`SQLiteMemoryStore`/`VectorMemoryStore` 可插拔替换；`MemoryManager` 是编排层，可独立演进而不动引擎。
- 遵守 ROADMAP 依赖方向，`memory` 不反向 import 工具/适配器。

### 12.7 与 ADPS 对照自检
| ADPS 判据 | 本项目结论 |
|-----------|-----------|
| 「是否每个 agent 都需要记忆服务？」 | 小镇 NPC **是**（偏好与经验动态变化、使用者不会手工维护）；通用 CLI 单轮任务可保持无状态 |
| 「稳定偏好可手工维护？」 | persona/性格走文件 + 版本化规则；动态经验走记忆服务（二者边界在 §3） |
| 「知识库/数据本体/控制平面分权」 | §3 边界表已明确，不混所有权 |
| 「envelope 字段完备」 | §4 覆盖 id/kind/scope/source/validity/trust/retrieval |
| 「五模式分工」 | M1 分层=§3；M2 RAG=§6 relevance；M3 进度=plan 引用控制平面（小镇 director 侧）；M4 失败日记=`reflection`+`trust.status=rejected` 反例库；M5 程序性=§3 `procedural` kind |

### 12.8 风险与预案
| 风险 | 影响 | 预案 |
|------|------|------|
| 检索质量差导致「断片」 | 前后矛盾 | 先三因子启发式，后 embedding；加反思层；保留 `persona` 恒常兜底 |
| 反思产物质量低/漂移 | 自我认知失真 | 反思产物先 `candidate`，准入后再 `accepted`；限制反思频率 |
| 记忆膨胀 | 检索变慢、噪音 | 准入淘汰 + `access_count` 冷启动降权 + 归档 `retired` |
| 记忆污染跨 NPC 扩散 | 群体错误经验 | `scope` 隔离默认关；群组共享走传播控制（P2） |
| 版本链过长 | 查询/存储开销 | 定期把「已取代且冷」的记忆物理归档 |

---

## 附：关键改动点速查（供后续实现，本文不落码）

| 文件 | 改动方向 |
|------|---------|
| `nono_agent/memory/models.py` | `Memory` → `MemoryRecord`（envelope，§4） |
| `nono_agent/memory/store.py` | 扩展 Protocol（§8）+ `SQLiteMemoryStore` |
| `nono_agent/memory/manager.py`（新增） | `MemoryManager`：准入/检索/反思/追溯编排 |
| `nono_agent/core/engine.py` | 注入 `MemoryManager`；`_build_system_prompt` 改检索注入 |
| `nono_agent/prompt/sections/memory_section.py` | 消费检索结果渲染 |
| `nono_agent/commands/builtin.py` | `/memory` 增检索/管理/溯源 |
| `nono_agent/pipeline/context_collapse.py` | 与反思共用 summarizer，落点分离（不强制改） |
