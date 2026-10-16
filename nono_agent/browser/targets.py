"""「侧边栏标签」与「CDP target」的换算，以及当前绑定的是哪一个。

**为什么需要这一层**：工具拿到的是面板的 tabId（我们自己 store 里的字符串），
agent-browser 要的是 CDP 的 targetId（32 位十六进制）。两边没有任何公共字段，
只能靠 ``/json/list`` 把它们对上。

**为什么不能靠 URL 硬配**：能对上，但用户同时开两个相同网址的标签时就二义了。
所以这里维护一个**显式绑定**：

- ``browser_open`` 新建标签时，走一次「前后 target 差集」，把新出现的那个
  targetId 直接记下 —— 这一刻是无歧义的；
- 之后按 URL 反查只是**兜底**，命中多个时如实报「有 N 个同样的页面，指不清」，
  绝默默挑第一个。

绑定是**进程级**的（一个 sidecar 进程 = 一个会话），不落盘、不跨会话。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from nono_agent.browser import cdp


class TargetResolutionError(RuntimeError):
    """换算不出来 —— 消息直接给模型看。"""


@dataclass(frozen=True)
class WebViewTarget:
    target_id: str
    url: str
    title: str


# 当前绑定的 tabId -> targetId。用锁保护：工具调用可能被并发发起
# （is_concurrent_safe=True 的那批），裸 dict 在 CPython 里也不一定崩，
# 但绑定是全局状态，值得按规矩上锁。
_lock = threading.Lock()
_bound_tab_id: str = ""
_bound_target_id: str = ""


def bind(tab_id: str, target_id: str) -> None:
    """记下「这个面板标签 = 这个 CDP target」。"""
    global _bound_tab_id, _bound_target_id
    with _lock:
        _bound_tab_id = tab_id
        _bound_target_id = target_id


def bound() -> tuple[str, str]:
    """当前绑定的 ``(tab_id, target_id)``，都没绑过时是两个空串。"""
    with _lock:
        return _bound_tab_id, _bound_target_id


def clear() -> None:
    """丢掉绑定。测试用；绑定失效时也不至于把上一个的 target 一直用下去。"""
    global _bound_tab_id, _bound_target_id
    with _lock:
        _bound_tab_id = ""
        _bound_target_id = ""


async def resolve(tab_id: str = "") -> str:
    """把面板 tabId 解析成 CDP targetId。

    ``tab_id`` 为空时用当前绑定。解析不出来就抛
    :class:`TargetResolutionError`，**不猜** —— 猜错意味着模型对着一个用户没
    选过的页面点下去，那比报错糟得多。
    """
    if not tab_id:
        current_tab, current_target = bound()
        if current_target:
            # 缓存的 target 也可能已经没了。导航一次就可能换（关标签、跨站跳转、
            # 用户自己在侧边栏关掉页面），不验活就返回，等于让调用方拿着一个死
            # target 去操作 —— 那正是「动作落到主窗口上」的起点。
            if await _alive(current_target):
                return current_target
            # 落到下面的 tabId 分支去重新解析（那里有 URL 反查兜底）。
            tab_id = current_tab
        else:
            raise TargetResolutionError(
                "No browser tab is bound yet. Use browser_open to open one, or "
                "browser_tabs to list the open tabs and browser_use to pick one."
            )

    # 命中原绑定时也要确认 target 还活着：标签可能已经被用户关掉了。
    current_tab, current_target = bound()
    if current_tab == tab_id and current_target:
        if await _alive(current_target):
            return current_target

    targets = await cdp.list_webviews()
    if not targets:
        # 「一个 target 都没有」有两种完全不同的原因，指不清就会把模型带沟里：
        # 端口压根没绑上（被上一次没退干净的进程占着），和端口好好的但一个标签
        # 都没开。前者去查端口，后者去 browser_open —— 先问一次端口。
        reason = await cdp.diagnose()
        if reason:
            raise TargetResolutionError(reason)
        raise TargetResolutionError(
            "CDP reports no browser tab targets. The app may have just restarted, "
            "or this tab has been closed."
        )

    # 兜底路径：靠面板报的 URL 反查。
    url = await _url_of_tab(tab_id)
    if url:
        hits = [
            str(item.get("id", ""))
            for item in targets
            if _same_page(str(item.get("url", "")), url)
        ]
        if len(hits) == 1:
            bind(tab_id, hits[0])
            return hits[0]
        if len(hits) > 1:
            raise TargetResolutionError(
                f"{len(hits)} tabs are all on {url}, so the target is ambiguous. "
                "Close the extras, or open a fresh one with browser_open."
            )

    # 兜底也指不准：至少把「现在到底有哪些」告诉模型，让它自己选。
    listing = "\n".join(
        f"- {item.get('title') or '(no title)'}  {item.get('url')}"
        for item in targets
        if item.get("id")
    )
    raise TargetResolutionError(
        f"Tab {tab_id} is gone (it may have been closed). Currently open:\n{listing}"
    )


async def _alive(target_id: str) -> bool:
    targets = await cdp.list_webviews()
    return target_id in {str(item.get("id", "")) for item in targets}


async def _url_of_tab(tab_id: str) -> str:
    """向渲染进程问这个 tabId 的地址（经总线，不走 CDP）。"""
    from nono_agent.browser import bridge

    reply = await bridge.request({"op": "tabs"}, timeout=3.0)
    if not reply.get("ok"):
        return ""
    tabs = reply.get("tabs")
    if not isinstance(tabs, list):
        return ""
    for tab in tabs:
        if isinstance(tab, dict) and str(tab.get("tabId") or "") == tab_id:
            return str(tab.get("url") or "")
    return ""


def snapshot_ids(targets: list[dict[str, object]]) -> set[str]:
    """取一批 target 的 id 集合 —— 差集法要用。"""
    return {str(item.get("id", "")) for item in targets if item.get("id")}


def new_since(before: set[str], after: list[dict[str, object]]) -> str:
    """「前后差集」里新增的那个 target 的 id；恰好一个时才返回。"""
    added = [
        str(item.get("id", ""))
        for item in after
        if item.get("id") and str(item.get("id")) not in before
    ]
    return added[0] if len(added) == 1 else ""


def _same_page(left: str, right: str) -> bool:
    """URL 比较：忽略末尾斜杠与 fragment。

    ``https://a.com`` 和 ``https://a.com/`` 在 CDP 里是两条不同的 target，
    但对用户来说是同一个页面；反过 ``#section`` 切换根本不改 target。

    **先切 fragment 再去斜杠**，顺序反了就不成立：``https://a.com/#top`` 先
    rstrip 去不掉斜杠，切完还带着 ``/``，跟 ``https://a.com`` 对不上。
    """
    return _normalize(left) == _normalize(right)


def _normalize(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/")
