"""内置浏览器的 CDP 端点探测。

端口由 Electron 主进程在 app ready 之前挂上，挂不挂取决于用户在插件面板的开关。
主进程把这个决定通过环境变量告诉 sidecar —— 这是「有没有这条能力」的**权威
答案**，比去戳一次端口更可靠（用户改了开关没重启时，两边会不一致，而那种情况
下端口本来也还没生效）。

工具侧靠它把「开不了的原因」说清楚：Agent 打开了一个标签却读不了页面时，
最怕它以为是网页坏了，然后开始瞎试。

``/json/list`` 也在这一层：它是「面板标签 → CDP target」的换算依据，比绕道
agent-browser CLI 便宜得多（一个 HTTP GET 对一次进程启动）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ENV_ENABLED = "NONO_BROWSER_CDP_ENABLED"
ENV_PORT = "NONO_BROWSER_CDP_PORT"
#: 主进程把 agent-browser 可执行文件的位置告诉 sidecar。打包版走
#: ``process.resourcesPath/agent-browser/...``，开发态指到解包出来的临时目录。
ENV_BINARY = "NONO_BROWSER_BIN"

#: 端口兜底值。**正常情况下用不上** —— 桌面壳让 Chromium 自选空闲端口后，会把
#: 真实端口通过 :data:`ENV_PORT` 传下来。留这个只是为了让「环境变量整个丢了」这种
#: 情况有个能说出口的号，而不是 0。
DEFAULT_PORT = 9222

# 探测超时给得很短：这是给模型看的状态，不是启动流程上的一步。
# 端点活着的话一个来回就够；它要是死了，多等也等不活。
_PROBE_TIMEOUT_S = 1.0

# 列 target 比探活要重一点（目标多时响应体不小），但也只该是一两个来回。
_TARGETS_TIMEOUT_S = 3.0


def is_enabled() -> bool:
    """主进程有没有把端口挂上。"""
    return os.environ.get(ENV_ENABLED, "").strip() == "1"


def port() -> int:
    try:
        return int(os.environ.get(ENV_PORT, "").strip() or DEFAULT_PORT)
    except ValueError:
        logger.warning("Invalid %s, falling back to %s", ENV_PORT, DEFAULT_PORT)
        return DEFAULT_PORT


def binary_path() -> str:
    """agent-browser 可执行文件路径；主进程没告诉我们就返回空串。"""
    return os.environ.get(ENV_BINARY, "").strip()


def unavailable_reason() -> str | None:
    """不用联网就能判定的两种「开不了」；正常时返回 ``None``。

    这层只看主进程注入的静态事实,所以是同步的。**它不能证明端口真的可用** ——
    主进程说「开了」只代表它请求过 Chromium 开，绑不绑得上是另一回事。
    要那层判断用 :func:`diagnose`。
    """
    if not is_enabled():
        return (
            "Agent control of the built-in browser is off. Enable the browser-agent "
            "plugin under Settings -> Plugins, then restart the app."
        )
    if not binary_path():
        return (
            "This build has no bundled agent-browser executable, so tabs can be "
            "opened but pages cannot be read. Put agent-browser into the app's "
            "resources directory (see apps/desktop/resources)."
        )
    return None


async def diagnose() -> str | None:
    """为什么现在不能操作网页；一切正常时返回 ``None``。

    比 :func:`unavailable_reason` 多问一层「端口到底通不通」，而且刻意把两种
    失败分开，因为解法完全不同：

    - 静态层就不通过 → 去插件面板拨开关 / 补二进制；
    - 静态层通过但端口连不上 → **端口被上一次没退干净的进程占着**。这种
      情况下端口号还是通着的，应答的却不是本应用，于是每个动作都超时，模型
      只会拿到一句 ``Operation timed out``，然后误以为是网页坏了。

    后者不区分「连不上」和「应答的不是本应用」：两者对用户是同一个动作 ——
    彻底退出应用再重开。
    """
    reason = unavailable_reason()
    if reason:
        return reason
    if await is_reachable():
        return None
    return (
        f"The debugging port 127.0.0.1:{port()} never bound, so pages cannot be "
        f"read and elements cannot be clicked. The usual cause is a process from a "
        f"previous run still holding the port: the port number still accepts "
        f"connections, but the answer comes from something other than this app, so "
        f"every operation times out. Quit the app fully (the tray menu's Quit, not "
        f"the window close button) and start it again. After restarting, check with: "
        f"Invoke-WebRequest http://127.0.0.1:{port()}/json/version"
    )


async def is_reachable() -> bool:
    """端口是否真的在听。

    主进程说开了、实际没开（端口被占、启动失败）时返回 False —— 这种情况下
    告诉模型「没开」比让它对着连接错误自己猜要省事。
    """
    if not is_enabled():
        return False
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            response = await client.get(f"http://127.0.0.1:{port()}/json/version")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


async def list_targets() -> list[dict[str, Any]]:
    """浏览器里所有可附着的 target。

    Electron 会把 ``<webview>`` 以 ``type="webview"`` 暴露出来（实测确认），而
    主窗口自己是 ``type="page"`` —— 这个区分是我们只碰侧边栏浏览器、不碰
    应用自身的唯一依据，所以过滤必须发生在工具侧，不能图省事全拿回来。
    """
    try:
        async with httpx.AsyncClient(timeout=_TARGETS_TIMEOUT_S) as client:
            response = await client.get(f"http://127.0.0.1:{port()}/json/list")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("Failed to list CDP targets", exc_info=True)
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and item.get("id")]


async def list_webviews() -> list[dict[str, Any]]:
    """只看侧边栏浏览器的 target。"""
    return [item for item in await list_targets() if item.get("type") == "webview"]
