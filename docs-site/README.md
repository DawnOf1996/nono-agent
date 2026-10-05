# 文档站

本目录是 Nono Agent 官方文档站的**项目根目录**，用 [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) 构建。
所有文档源码在 `docs/` 下，按主题分目录；站点配置是 `mkdocs.yml`。

## 本地预览

```bash
uv sync --extra dev --group docs
uv run mkdocs serve -f docs-site/mkdocs.yml
```

默认起在 <http://127.0.0.1:8000>，改文件即时热重载。

!!! warning "别用 `uv sync --group docs` 单独跑"
    `uv sync` 是精确同步，不带 `--extra dev` 会把 pytest / ruff / mypy 和 `apps/*` 一起卸掉。
    日常开发请始终写全 `uv sync --extra dev --group docs`。

## 构建

```bash
uv run mkdocs build -f docs-site/mkdocs.yml            # 产物在仓库根的 site/（已 gitignore）
uv run mkdocs build -f docs-site/mkdocs.yml --strict   # 警告即失败，CI 用这个
```

`--strict` 会在出现失效内部链接、页面没进导航等告警时直接失败 —— CI 跑的就是这条命令。

## 发布

推到 `main` 后由 CI 构建（`mkdocs build --strict`，只校验不发布）。

### 部署到 Gitee Pages

Gitee Pages 从 `gh-pages` 分支取静态文件。**注意 `mkdocs gh-deploy` 只支持 GitHub**，
你的远端是 Gitee，要手动构建 + 推送：

```bash
# 1. 先在 mkdocs.yml 里设 base（子路径部署必须设，否则线上资源全 404）
#    base: /nono-agent/

# 2. 构建
uv run mkdocs build -f docs-site/mkdocs.yml

# 3. 把 site/ 推到 gh-pages 分支
git subtree push --prefix site origin gh-pages
```

然后在 Gitee 仓库「服务 → Gitee Pages」把分支选为 `gh-pages`。

部署在域名根（自己买了域名、`docs.example.com` 这种）时，`base` 留空或写 `""`，
并在 `site_url` 填真实地址——它影响 canonical 链接与 sitemap。

## 目录结构

```text
docs-site/
├── mkdocs.yml             # 站点配置：导航、主题、Markdown 扩展
├── nono-agent.code-workspace   # VS Code 工作区
└── docs/
    ├── index.md           # 首页
    ├── getting-started/   # 快速开始、安装、配置与密钥
    ├── guide/             # 使用手册
    ├── architecture/      # 架构设计
    ├── development/       # 开发指南
    ├── roadmap/           # 路线图
    └── research/          # 研究笔记与历史方案
```

## 写文档的约定

1. **每个文件开头写 YAML front matter**，至少给 `title`，否则侧边栏显示的是文件名：

   ```yaml
   ---
   title: 会话事件目录
   ---
   ```

2. **新页面必须进 `mkdocs.yml` 的 `nav`**，否则 `--strict` 构建会失败。

3. **内部链接用相对路径**（`../guide/usage.md`）。指向仓库里非文档文件（代码、脚本）的链接，
   MkDocs 无法校验，写成 Gitee 上的绝对 URL 更稳。

4. **代码块标明语言**，Mermaid 图用 ` ```mermaid `（已在 `mkdocs.yml` 里配好自定义 fence）。

5. **文档里的事实要能在代码里找到出处。** 类名、函数名、配置字段、端点、命令行参数都必须与代码一致；
   写之前先 grep 确认。审查文档与代码的一致性是这套文档的硬要求，不是可选项。

## 依赖

在根 `pyproject.toml` 的 `[dependency-groups] docs` 里声明：

```toml
[dependency-groups]
docs = [
    "mkdocs-material>=9.5",
    "mkdocs-glightbox>=0.4",
]
```
