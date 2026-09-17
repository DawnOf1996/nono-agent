# Git 提交规范

本仓库采用 [Conventional Commits](https://www.conventionalcommits.org/) 风格，便于生成变更日志、按类型筛选历史，并与 Roadmap 模块边界对齐。

## 1. 格式

```
<type>(<scope>): <subject>

[optional body]

[optional footer]
```

### 规则

| 部分 | 要求 |
|------|------|
| `type` | 必填，小写，见下表 |
| `scope` | 可选，小写，见「常用 scope」；多模块用逗号或取主模块 |
| `subject` | 必填；祈使句、现在时；**不加句号**；建议 ≤ 72 字符 |
| 正文 | 可选；说明动机与行为变化，而非罗列文件名 |
| footer | 可选；`BREAKING CHANGE:`、`Fixes #123` 等 |

语言：`subject` / `body` 优先 **中文**（与团队沟通一致）；专有名词、API、包名保持原文（如 `QueryEngine`、`submit_message`）。

## 2. type 类型

| type | 含义 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(tools): 新增 Bash 内置工具` |
| `fix` | 缺陷修复 | `fix(core): 权限确认等待时 abort 不生效` |
| `docs` | 仅文档 | `docs(roadmap): 补充完整包树与职责说明` |
| `refactor` | 重构（无行为意图变更） | `refactor(tools): 将 builtin 拆为包` |
| `perf` | 性能 | `perf(pipeline): 降低 microcompact 扫描开销` |
| `test` | 测试 | `test(engine): 补充 resume 边界用例` |
| `chore` | 杂项（依赖、忽略文件、脚本） | `chore: 添加 ruff 配置` |
| `build` | 构建 / 打包 | `build: 调整 hatch 打包包含路径` |
| `ci` | CI 配置 | `ci: 增加 pytest 工作流` |
| `style` | 格式（不影响逻辑） | `style: ruff format` |
| `revert` | 回滚 | `revert: 回滚 feat(mcp) 相关提交` |

破坏性变更：在 `type` 后加 `!`，或在 footer 写 `BREAKING CHANGE:`。

```
feat(sdk)!: SDKResult 增加 permission_denials 字段

BREAKING CHANGE: 消费方若按固定字段反序列化需同步升级。
```

## 3. 常用 scope（与包树对齐）

优先使用 `nono_agent/` 下的包名或文档名：

| scope | 对应 |
|-------|------|
| `core` | `nono_agent/core` |
| `pipeline` | 预处理管线 |
| `recovery` | 恢复链 |
| `hooks` | 引擎内 hooks |
| `cli` / `server` | 产品 CLI / Server（`apps/`） |
| `providers` | LLM Provider |
| `session` | MessageStore / Transcript |
| `tools` | 工具协议与 builtin |
| `prompt` | Prompt 组装 |
| `rules` / `skills` / `plugins` | 扩展面 |
| `commands` | Slash |
| `settings` / `permissions` | 配置与权限 |
| `mcp` / `agents` | MCP / 子 Agent |
| `memory` / `checkpoint` | P2 能力 |
| `observability` | Token/日志/指标 |
| `docs` | `docs/`、README（也可直接用 type=docs 省略 scope） |
| `roadmap` / `arch` | 规划或架构文档专项 |

无合适 scope 时可省略：`chore: 升级 openai SDK`。

## 4. 示例

**好的：**

```
feat(permissions): 引入 PermissionPolicy 与 default 模式

将 CLI/Server 内联布尔回调收拢为可注入策略，为 Rule 文件预留解析入口。
```

```
fix(cli): 去掉硬编码 API Key，改为环境变量读取
```

```
docs(arch): 同步完整包树与各包职责
```

```
refactor(tools): builtin.py 拆分为 tools/builtin 包

保留 tools/builtin.py 薄 re-export，避免打断现有 import。
```

**避免：**

```
update code
fix bug
WIP
提交
```

## 5. 提交内容约定

- **一个提交一个意图**：功能、修复、纯文档尽量分开（大规模文档对齐可单次 `docs`）。
- **不提交密钥**：`.env`、真实 `api_key`、凭证文件不得入库；发现后立刻轮换密钥。
- **不提交无关产物**：`__pycache__`、`.venv`、本地 session 文件等（已由 `.gitignore` 覆盖则勿强行 add）。
- **API / SDK 破坏性变更**必须在 footer 标明 `BREAKING CHANGE`。
- 合并前本地至少：相关测试通过；文档变更与代码意图一致。

## 6. 分支与 PR（简要）

| 项 | 建议 |
|----|------|
| 主分支 | `main`（或仓库默认分支） |
| 功能分支 | `feat/<scope>-<简述>`，如 `feat/tools-bash` |
| 修复分支 | `fix/<scope>-<简述>` |
| PR 标题 | 与最终 squash/merge 的 commit 标题同规范 |
| PR 描述 | Summary + Test plan；关联 Roadmap 章节更佳 |

若使用 squash merge：以 PR 标题作为最终 commit 标题，务必符合本规范。

## 7. 相关文档

| 文档 | 说明 |
|------|------|
| [ROADMAP.md](./ROADMAP.md) | 能力规划与包职责 |
| [NonoAgent-Architecture.md](./NonoAgent-Architecture.md) | 系统架构 |
| [README.md](../README.md) | 项目入口 |
