---
title: 安装
---

# 安装

## 环境要求

| 项目 | 要求 |
|---|---|
| Python | **3.12+**（`ruff` target 是 py311，但运行时按 3.12 特性写） |
| 包管理 | 推荐 [uv](https://docs.astral.sh/uv/)；也可用 pip |
| API Key | 对应 Provider 的密钥（`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / 引导时填写的自定义供应商 Key） |
| Node.js | 仅打包 Electron 桌面版与 `npx` 形式的 MCP server 时需要 |

## 从源码安装

```bash
git clone https://gitee.com/dawn1996/nono-agent.git
cd nono-agent

# 运行时依赖
uv sync

# 运行时 + 开发工具（pytest / ruff / mypy）+ 产品层（apps/cli、apps/server）
uv sync --extra dev
```

`--extra dev` 会把 `apps/cli` 与 `apps/server` 以可编辑模式装进同一个虚拟环境，因此 `uv run nono` 和 `uv run nono-app-server` 在仓库根直接可用。

## 用 pip 安装

```bash
pip install -e ".[dev]"
```

!!! warning "Windows 上的 shell 能力"
    `bash` 工具在 Windows 上退化为 PowerShell 能力缝（`nono_agent/shell/` 里声明方言与编码）。需要完整的类 Unix 语义请在 WSL 或容器内运行。

## 文档站依赖（可选）

```bash
uv sync --extra dev --group docs
```

只装文档站的话用 `uv sync --group docs`；但它会移除 `--extra dev` 带来的产品层依赖，日常开发请始终带上 `--extra dev`。

!!! tip "关于 `uv sync` 的一个坑"
    `uv sync` 默认是**精确同步**：不带 `--extra dev` 会把 pytest / ruff / mypy 以及 `apps/*` 一起卸掉。如果你只想补装某个东西，用 `uv sync --extra dev --group <名字>` 这样的完整组合，别指望它做增量安装。

## 从库代码里确认安装成功

```bash
uv run python -c "from importlib.metadata import version; print(version('nono-agent'))"
```

> 引擎包目前没有导出 `nono_agent.__version__`，版本号只存在于 `pyproject.toml` 的 `project.version` 与 wheel 元数据里。

## 下一步

- 需要先配好模型供应商 → [配置与密钥](configuration.md)
- 只想先跑一次对话 → [快速开始](quickstart.md)
