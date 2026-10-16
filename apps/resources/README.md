# apps/resources —— 可外挂的第三方输入

这里放**打包时需要、但不是我们自己的代码产物**的东西：第三方可执行文件、
预编译的静态资源之类。**这些文件提交进 git**，任何人 clone 完就能直接打包，
不需要联网，也不会因为上游 registry 抖一下就打不出包。

## 什么时候该放这儿

同时满足三条：

1. 它是**输入**，不是构建产物（`nono-app-server.exe` 是 PyInstaller 冻出来的、
   `web/` 是 vite 出的，那些都不算）；
2. 打包后的安装包**需要**它，缺了某个功能就残废；
3. 它**不该**在每次打包时重新生成。

## 什么时候不该

能被本仓库的构建流程产出来的东西，都不该放这儿 —— 放进来等于给
`pack-desktop.ps1` 的组装段添一个必须手工维护的分支。

## 和 apps/desktop/resources/ 的区别

两个目录名字相近，作用**完全相反**，别搞混：

| | apps/resources/ | apps/desktop/resources/ |
|---|---|---|
| 身份 | **输入** | **组装产物** |
| 谁写 | 人 / 取件脚本 | `pack-desktop.ps1` |
| 生命周期 | 长期存在，提交进 git | **每次打包先整个删掉重建** |
| git | 受版本控制 | `.gitignore` 忽略 |
| 内容 | 第三方二进制 | 从 `dist/` 和 `web/dist` 拷来的构建产物 |

把东西误放进 `apps/desktop/resources/` 的后果不是「少见一次」那么简单：
下一次 `pack-desktop.ps1` 跑起来时它会被 `Remove-Item -Recurse -Force` 静静
清掉，等到发现的时候已经晚了 —— 而且 `extraResources` 会指向一个不存在的目录，
报错还发生在离原因十万八千里的地方。

## 现有的条目

### agent-browser/

`agent-browser-win32-x64.exe`（约 17 MB，Apache-2.0，vercel-labs）。

`browser-agent` 工具插件**交互那一半**依赖它：读页面结构、按 ref 点击、往输入框
填字。只有「开标签 / 列标签」那部分不依赖它 —— 那条路只走渲染进程的 SSE 总线。

- **为什么是二进制而不是 npm 依赖**：这个包只发布平台相关的 `.exe`，没有可用的
  JS 入口；引它进来会多出一整个 npm 依赖树（51 MB 的 tarball 只为了换一个
  17 MB 的可执行文件）。
- **为什么提交进 git 而不是 CI 现下**：见本文开头。代价是仓库大 17 MB，且进了
  历史就一直在 —— 要换掉得 `git rm` + `filter-repo`。
- **怎么来的**：见 `scripts/fetch_agent_browser.ps1`。**升级版本只改那个脚本**
  （版本号钉死在它的参数默认值里），然后用 `-Force` 重下一份、跑一遍冒烟测试再
  提交。这里写死 0.39.1 是因为它是实测过能驱动 Electron `<webview>` 的版本；
  跟着 latest 漂等于每次打包都在换一条没测过的路。

打包时由 `pack-desktop.ps1` 从这里拷进 `apps/desktop/resources/agent-browser/`。
拷之前目录无条件建好 —— electron-builder 碰上 `extraResources` 指向不存在的
目录，报错跟原因完全脱节。

开发态（`pnpm dev:desktop`）主进程直接指这里，不用等打包。
