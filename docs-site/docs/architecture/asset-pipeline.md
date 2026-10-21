# 图片资产管线

对话里的图片不再以 base64 形态躺在会话文件里，而是落成磁盘文件，会话里只留一个
**资产引用**；字节在请求发出的那一刻才从磁盘读回来。

## 布局

```
<数据目录>/                 # user_home()，默认 ~/.nono-agent
├── sessions/               # 现有
└── assets/                 # 新增，与 sessions 同级
    └── 2026/10/10/
        ├── 15-32-05-614-asset_20261010-153205-614_<hash12>_<rand8>-image.png
        ├── 15-32-05-614-…-image.png.asset.json    # 单个资产的元数据
        └── assets.jsonl                            # 当天资产的追加式索引
```

资产 ID 格式：`asset_<YYYYMMDD>-<HHMMSS>-<毫秒>_<sha256前12位>_<随机8位>`。
文件名 = `<时-分-秒-毫秒>-<资产ID>-<原名>`（Windows 不能有冒号）。

数据目录搬走时 assets 跟着走 —— 它是数据目录的派生量，不需要单独配置。

## 链路

| 阶段 | 位置 | 做什么 |
|---|---|---|
| 上传 / 落盘 | `nono_agent/assets/store.py` | 字节写进资产目录，产出 `AssetRef` |
| 提交 | `core/engine.py::submit_message` | base64 附件 → 资产引用（**日志里从此没有 base64**） |
| 存储 | `core/types.py` / `session/store.py` | 会话状态与事件流里只有引用 |
| 水合 | `nono_agent/assets/materialize.py` | 请求发出前，引用 → base64 |
| 读图 | `tools/builtin/read_image.py` | 模型按路径 / 资产 ID 把图读进对话 |

### 水合为什么不在预处理管线里

预处理管线跑的是 `MessageStore.snapshot_for_request()` 的深拷贝，但**本轮的用户
消息是在管线之后才追加进请求列表的**（见 `core/loop.py`）。挂在管线上，本轮贴
进来的图在第一次请求里就丢了。所以水合放在「消息全部就位、即将发给 provider」
的那一刻 —— 历史消息与本轮消息一次覆盖。

水合只改请求用的深拷贝，store 与事件流里留的始终是引用。

### 工具怎么把图交给模型

`ToolResult.images` 是一条独立通道（与 `ui` 相反：`ui` 不进上下文，`images` 进）。
图片**不**放进 `tool_result.content` —— OpenAI 的 tool role 与 Responses 的
`function_call_output` 只收字符串。引擎把图片块单独作为一条 user 消息发出，
那是 Anthropic / OpenAI Chat / OpenAI Responses 三家都认的形状。

## 约束

| # | 约束 | 位置 | 后果 |
|---|---|---|---|
| C1 | 资产 ID 是定位文件的**唯一**入口；客户端给的相对路径不参与定位 | `assets/store.py::path_for` | 客户端只能引用已存在的资产，不能指任意路径 |
| C2 | 资产 ID 过严格正则后由时间戳反推目录 | `assets/store.py::ASSET_ID_RE` | 路径穿越（`../`）在入口就被拒 |
| C3 | 图片类型按**魔数**判定，不信扩展名 | `assets/imageinfo.py::sniff_media_type` | 扩展名与内容不符时按内容走，provider 不会拿到「PNG 壳的 JPEG」 |
| C4 | 宽高在落盘时探测并写进引用 | `assets/imageinfo.py::probe_size` | 图片 token 按 `(w×h)/750` 折算；不按 base64 长度（那会高估十几倍） |
| C5 | 体积上限 20MB | `assets/store.py::MAX_ASSET_BYTES` | 与前端「图片不能超过 20MB」一致，服务端再兜一次 |
| C6 | 资产 API 在 `/asset`（单数） | `apps/server/.../routers/assets.py` | `/assets`（复数）归打包后的前端产物 —— 同名会把 JS chunk 吃掉，前端白屏 |
| C7 | 老会话里的 `source.type == "base64"` 原样透传 | `assets/materialize.py` | 不需要迁移旧会话 |

## HTTP 接口

| 端点 | 用途 |
|---|---|
| `POST /asset` | 上传一张图片（multipart），返回资产引用 |
| `GET /asset/{asset_id}` | 取回图片字节（界面回放历史消息时用） |

前端在发消息前先把图片传上来，`/chat` 的附件里只带资产 ID。

## 工具

`read_image` —— 把磁盘上的图片读进对话，让模型能看见它。接受三种入参：

- 资产 ID（`asset_20260101-120000-000_ab12cd34ef56_0123abcd`）
- 资产目录相对路径（`assets/2026/01/01/xxx.png`）
- 工作区路径（过 `context.resolve_path` 沙箱）

资产目录里的文件直接引用（不复制）；工作区里的文件收编进资产目录，之后都走
「资产引用」这一条路径。

## 不做的事

- **资产 GC**（按保留期清理孤儿文件）—— 尚未实现。`assets.jsonl` 索引与
  `asset_id` 里带的时间戳已经足够算出孤儿，留给后续。
